"""Real PG atomic starts, signals, root limits and current owner authorization."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap.extensions import ExtensionScopes
from opsmesh.bootstrap.orchestration import ExecutionAssets, execution_bindings
from opsmesh.bootstrap.routers import (
    ConfigurationExecutionScopeAdmission,
    ConfigurationScopeAdmission,
)
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.definitions.contracts import (
    FlowDefinitionSpec,
    FlowGraph,
    FlowLimits,
    FlowNode,
    FlowSignal,
    StepCommand,
)
from opsmesh.orchestration.definitions.schemas import DefinitionCreate
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.runs.activities import FlowActivities
from opsmesh.orchestration.runs.admission import reserve_root_bound
from opsmesh.orchestration.runs.models import FlowRun, FlowSignalReceipt
from opsmesh.orchestration.runs.schemas import FlowControl, FlowRunCreate
from opsmesh.orchestration.runs.service import control_run, start_run, submit_signal
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from tests.conftest import dispatch_cipher


@pytest.fixture
def flow_facts(database_sessions, tmp_path):
    cipher = dispatch_cipher()
    admission = ConfigurationExecutionScopeAdmission()
    runtime = FlowActivities(
        database_sessions,
        UserIdentityDirectory(),
        ExtensionScopes(admission, cipher),
        admission,
        cipher,
        ExecutionAssets(tmp_path, ConfigurationScopeAdmission()),
        execution_bindings(),
    )
    with database_sessions() as session, session.begin():
        user = create_user(session, email=f"flow-{uuid4()}@example.com", display_name="Flow")
        actor = runtime.users.load_identity(session, user.id)
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="Flow",
                spec=FlowDefinitionSpec(
                    graph=FlowGraph(
                        nodes=[
                            FlowNode(
                                key="signal", kind="wait", wait_kind="signal", signal_name="answer"
                            )
                        ],
                        output={"$ref": "steps.signal"},
                    ),
                    limits=FlowLimits(model_tokens=100),
                ),
            ),
        )
        revision = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        return runtime, actor, revision.id


def create_flow(database_sessions, facts, key="start"):
    runtime, actor, revision = facts
    with database_sessions() as session, session.begin():
        record = start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=revision, input={}),
            idempotency_key=key,
            cipher=runtime.cipher,
        )
        return record.id


def test_webhook_key_rotation_preserves_secret_and_trigger_revision(database_sessions, flow_facts):
    from cryptography.fernet import Fernet

    from opsmesh.orchestration.triggers.models import FlowTrigger
    from opsmesh.orchestration.triggers.service import reencrypt_webhook_secrets
    from opsmesh.shared.security.encryption import SecretCipher

    runtime, actor, revision = flow_facts
    next_cipher = SecretCipher(
        key=Fernet.generate_key().decode(),
        key_id="next",
        previous_keys={"test": "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="},
    )
    with database_sessions() as session, session.begin():
        trigger = FlowTrigger(
            principal_id=actor.user_id,
            initiated_by_id=actor.user_id,
            revision_id=revision,
            kind="webhook",
            configuration={},
            secret_ciphertext=runtime.cipher.encrypt_payload({"secret": "signature"}).ciphertext,
        )
        session.add(trigger)
        session.flush()
        identifier, original = trigger.id, trigger.secret_ciphertext
    with database_sessions() as session, session.begin():
        assert reencrypt_webhook_secrets(session, cipher=next_cipher, cursor=None, limit=100) == (
            1,
            None,
        )
    with database_sessions() as session:
        trigger = session.get(FlowTrigger, identifier)
        assert trigger.revision == 1 and trigger.secret_ciphertext != original
        assert next_cipher.decrypt_payload(trigger.secret_ciphertext, key_id="next") == {
            "secret": "signature"
        }


def test_concurrent_start_has_one_flow_and_one_outbox(database_sessions, flow_facts):
    from opsmesh.shared.db.idempotency import IdempotencyInProgressError

    def compete(_):
        try:
            return create_flow(database_sessions, flow_facts)
        except IdempotencyInProgressError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        identifiers = list(executor.map(compete, range(2)))
    identifier = create_flow(database_sessions, flow_facts)
    assert set(identifiers) <= {None, identifier} and identifier in identifiers
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.workflow_id.like("flow:%"))
            )
            == 1
        )


def test_rollback_has_no_run_or_dispatch(database_sessions, flow_facts):
    runtime, actor, revision = flow_facts
    with pytest.raises(RuntimeError), database_sessions() as session, session.begin():
        start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=revision, input={}),
            idempotency_key="rollback",
            cipher=runtime.cipher,
        )
        raise RuntimeError("Abort the business transaction")
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.workflow_id.like("flow:%"))
            )
            == 0
        )


def test_early_signal_is_durable_and_stale_generation_is_rejected(database_sessions, flow_facts):
    runtime, actor, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)
    signal = FlowSignal(
        command_id=uuid4(), step_path="signal", generation=1, name="answer", value={"ok": True}
    )
    with database_sessions() as session, session.begin():
        submit_signal(
            session, actor=actor, run_id=identifier, request=signal, cipher=runtime.cipher
        )
        submit_signal(
            session, actor=actor, run_id=identifier, request=signal, cipher=runtime.cipher
        )
    with pytest.raises(InvalidArgumentError), database_sessions() as session, session.begin():
        submit_signal(
            session,
            actor=actor,
            run_id=identifier,
            request=signal.model_copy(update={"generation": 2}),
            cipher=runtime.cipher,
        )
    runtime.start_step(
        StepCommand(
            run_id=identifier,
            path="signal",
            node=FlowNode(key="signal", kind="wait", wait_kind="signal", signal_name="answer"),
            arguments={},
        )
    )
    result = runtime.probe(identifier, "signal")
    assert result["status"] == "completed" and result["result"] == {"ok": True}
    with database_sessions() as session, session.begin():
        submit_signal(
            session, actor=actor, run_id=identifier, request=signal, cipher=runtime.cipher
        )
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        submit_signal(
            session,
            actor=actor,
            run_id=identifier,
            request=signal.model_copy(update={"command_id": uuid4()}),
            cipher=runtime.cipher,
        )
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowSignalReceipt)) == 1


def test_root_bounds_are_atomic_and_retry_does_not_reset_them(database_sessions, flow_facts):
    runtime, actor, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)
    with database_sessions() as session, session.begin():
        reserve_root_bound(
            session,
            root_id=identifier,
            operation_id="a",
            tokens=60,
        )
        reserve_root_bound(
            session,
            root_id=identifier,
            operation_id="a",
            tokens=60,
        )
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        reserve_root_bound(
            session,
            root_id=identifier,
            operation_id="b",
            tokens=41,
        )
    with database_sessions() as session, session.begin():
        run = session.get(FlowRun, identifier)
        run.status = "needs_attention"
        session.flush()
        control_run(
            session,
            actor=actor,
            run_id=identifier,
            cipher=runtime.cipher,
            request=FlowControl(command_id=uuid4(), expected_revision=run.revision, action="retry"),
        )
        assert run.allocated_tokens == 60
        assert run.workflow_generation == 2


def test_trigger_receipt_and_run_rollback_and_replay(database_sessions, flow_facts):
    from opsmesh.orchestration.triggers.models import FlowTrigger, TriggerReceipt
    from opsmesh.orchestration.triggers.schemas import TriggerConfiguration, TriggerCreate
    from opsmesh.orchestration.triggers.service import create_trigger, deliver

    runtime, actor, revision = flow_facts
    with database_sessions() as session, session.begin():
        trigger = create_trigger(
            session,
            actor=actor,
            request=TriggerCreate(
                revision_id=revision,
                kind="webhook",
                webhook_secret="x" * 32,
                configuration=TriggerConfiguration(),
            ),
            cipher=runtime.cipher,
        )
        identifier = trigger.id
    with pytest.raises(RuntimeError), database_sessions() as session, session.begin():
        deliver(
            session,
            runtime=runtime,
            trigger=session.get(FlowTrigger, identifier),
            event_id="rolled-back",
            payload={"item": 1},
        )
        raise RuntimeError("The source transaction failed")
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(TriggerReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.workflow_id.like("flow:%"))
            )
            == 0
        )
    with database_sessions() as session, session.begin():
        trigger = session.get(FlowTrigger, identifier, with_for_update=True)
        first = deliver(session, runtime=runtime, trigger=trigger, event_id="one", payload={})
        second = deliver(session, runtime=runtime, trigger=trigger, event_id="one", payload={})
        assert first.id == second.id
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        deliver(
            session,
            runtime=runtime,
            trigger=session.get(FlowTrigger, identifier),
            event_id="one",
            payload={"changed": 1},
        )
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 1
        assert session.scalar(select(func.count()).select_from(TriggerReceipt)) == 1


def test_child_depth_ancestor_and_completed_root_bounds(database_sessions, flow_facts):
    runtime, actor, revision = flow_facts
    identifier = create_flow(database_sessions, flow_facts)
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=revision),
            idempotency_key="recursive-child",
            parent=session.get(FlowRun, identifier),
            cipher=runtime.cipher,
        )
    with database_sessions() as session, session.begin():
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="Another target",
                spec=FlowDefinitionSpec(graph=FlowGraph(nodes=[FlowNode(key="wait", kind="wait")])),
            ),
        )
        other = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        other_id = other.id
        root = session.get(FlowRun, identifier)
        root.limits = {**root.limits, "max_depth": 1}
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=other_id),
            idempotency_key="too-deep",
            parent=session.get(FlowRun, identifier),
            cipher=runtime.cipher,
        )
    with database_sessions() as session, session.begin():
        root = session.get(FlowRun, identifier)
        root.status = "running"
        reserve_root_bound(
            session,
            root_id=root.id,
            operation_id="late-job",
            tokens=100,
        )
    with pytest.raises(ConflictError), database_sessions() as session, session.begin():
        reserve_root_bound(
            session,
            root_id=identifier,
            operation_id="late-overflow",
            tokens=1,
        )


def test_revoked_calendar_occurrence_has_one_rejection(database_sessions, flow_facts):
    from concurrent.futures import ThreadPoolExecutor

    from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
    from opsmesh.identity.users.models import UserProfile
    from opsmesh.orchestration.triggers.activities import TriggerActivities
    from opsmesh.orchestration.triggers.models import TriggerReceipt
    from opsmesh.orchestration.triggers.schemas import TriggerConfiguration, TriggerCreate
    from opsmesh.orchestration.triggers.service import create_trigger

    runtime, actor, revision = flow_facts
    with database_sessions() as session, session.begin():
        trigger = create_trigger(
            session,
            actor=actor,
            request=TriggerCreate(
                revision_id=revision,
                kind="calendar",
                configuration=TriggerConfiguration(interval_seconds=60),
            ),
            cipher=runtime.cipher,
        )
        payload = {"trigger_id": str(trigger.id), "revision": trigger.revision, "occurrence": "one"}
        session.get(UserProfile, actor.user_id).status = "disabled"
    activities = TriggerActivities(
        runtime, AuthoritativeEvents(runtime.cipher, runtime.scopes), None, "unused"
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(activities.fire_calendar, [payload] * 4))
    assert all(item["status"] == "rejected" for item in results)
    assert activities.fire_calendar(payload)["status"] == "rejected"
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(TriggerReceipt)) == 1
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 0


def test_concurrent_unauthorized_source_has_one_rejection(database_sessions, flow_facts):
    from concurrent.futures import ThreadPoolExecutor

    from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
    from opsmesh.orchestration.triggers.activities import TriggerActivities
    from opsmesh.orchestration.triggers.models import TriggerReceipt
    from opsmesh.orchestration.triggers.schemas import (
        SourceEventWork,
        TriggerConfiguration,
        TriggerCreate,
    )
    from opsmesh.orchestration.triggers.service import create_trigger

    runtime, actor, revision = flow_facts
    with database_sessions() as session, session.begin():
        create_trigger(
            session,
            actor=actor,
            request=TriggerCreate(
                revision_id=revision,
                kind="event",
                configuration=TriggerConfiguration(
                    source_kind="extension", event_type="unavailable.changed", instance_id=uuid4()
                ),
            ),
            cipher=runtime.cipher,
        )
    identifier = uuid4()
    reference = SourceEventWork(
        execution_id=identifier,
        workflow_id=f"source:{identifier}",
        source_kind="extension",
        source_id=uuid4(),
    )
    activities = TriggerActivities(
        runtime, AuthoritativeEvents(runtime.cipher, runtime.scopes), None, "unused"
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(activities.consume_source, [reference] * 4))
    assert all(item["accepted"] == 0 for item in results)
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(TriggerReceipt)) == 1
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 0
