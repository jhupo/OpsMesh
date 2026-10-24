"""Catalog releases, cost reservations and run/Outbox atomicity on PostgreSQL."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.agents.catalog import service as catalog
from opsmesh.agents.catalog.models import CatalogEntry
from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.config.model import ModelRef
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.agents.providers.models import ModelInfo
from opsmesh.bootstrap.automation import dispatch_evaluation, target_ports
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.execution.instances.schemas import InstanceCreate
from opsmesh.execution.instances.service import create_instance
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.platform.billing import service as billing
from opsmesh.platform.billing.contracts import ModelPricing
from opsmesh.platform.billing.models import SpendingAccount, SpendingLimit
from opsmesh.platform.evaluations import service as evaluations
from opsmesh.platform.evaluations.contracts import (
    DatasetCase,
    DatasetSpec,
    EvaluationTarget,
    EvaluatorSpec,
    ExperimentSpec,
)
from opsmesh.platform.evaluations.evaluators import default_evaluators
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.users.service import create_user, load_identity
from opsmesh.workflows.triggers import service as triggers
from opsmesh.workflows.triggers.contracts import TriggerSpec


@pytest.fixture
def actors(database_sessions):
    with database_sessions.begin() as session:
        identities = []
        for name, role in (("admin", "admin"), ("alice", "member"), ("bob", "member")):
            row = create_user(
                session,
                email=f"{name}@example.com",
                display_name=name,
                platform_roles=frozenset({role}),
            )
            identities.append(load_identity(session, row.id))
        return identities


def agent(identifier="private", instructions="first"):
    return AgentDefinition(
        id=identifier,
        config=AgentConfig(
            name="Agent", instructions=instructions, model=ModelRef(provider_id="p", model_id="m")
        ),
    )


def test_catalog_release_rollback_and_private_visibility(database_sessions, actors):
    cipher = database_sessions.kw["info"]["dispatch_cipher"]
    admin, alice, bob = actors
    provider = ProviderConfig(
        id="p",
        name="Provider",
        credential_ref="env:MODEL_KEY",
        models=[ModelInfo(id="m", max_output_tokens=128)],
    )
    with database_sessions.begin() as session:
        catalog.save_draft(
            session,
            actor=admin,
            cipher=cipher,
            kind="provider",
            value=provider,
            expected_revision=0,
            redaction=PLATFORM_REDACTION,
        )
        catalog.publish(
            session,
            actor=admin,
            cipher=cipher,
            kind="provider",
            identifier="p",
            expected_revision=1,
        )
        catalog.save_draft(
            session,
            actor=alice,
            cipher=cipher,
            kind="agent",
            value=agent(),
            expected_revision=0,
            redaction=PLATFORM_REDACTION,
        )
        first = catalog.publish(
            session,
            actor=alice,
            cipher=cipher,
            kind="agent",
            identifier="private",
            expected_revision=1,
        )
        first_digest = first.digest
    with database_sessions.begin() as session:
        assert (
            catalog.definition_snapshot(session, cipher, alice)("private").config.instructions
            == "first"
        )
        with pytest.raises(NotFoundError):
            catalog.definition_snapshot(session, cipher, bob)("private")
        catalog.save_draft(
            session,
            actor=alice,
            cipher=cipher,
            kind="agent",
            value=agent(instructions="second"),
            expected_revision=2,
            redaction=PLATFORM_REDACTION,
        )
        catalog.publish(
            session,
            actor=alice,
            cipher=cipher,
            kind="agent",
            identifier="private",
            expected_revision=3,
        )
        restored = catalog.publish(
            session,
            actor=alice,
            cipher=cipher,
            kind="agent",
            identifier="private",
            expected_revision=4,
            restore_number=1,
        )
        assert restored.number == 3 and restored.digest == first_digest
        assert session.get(CatalogEntry, ("agent", "private")).published_number == 3


def test_monthly_cost_reservations_are_admitted_and_settled(database_sessions, actors):
    _, alice, _ = actors
    run_id = uuid4()
    with database_sessions.begin() as session:
        session.add(
            ExecutionRun(
                id=run_id,
                user_id=alice.user_id,
                kind="agent",
                request_key="billing",
                fingerprint="0" * 64,
                encrypted_input="opaque",
                key_id="test",
                status="running",
            )
        )
        session.add(SpendingLimit(user_id=alice.user_id, currency="USD", amount=Decimal(2)))
        session.flush()
        billing.reserve(
            session,
            operation_id="model:1",
            run_id=run_id,
            user_id=alice.user_id,
            provider_id="p",
            model_id="m",
            pricing=ModelPricing(input_per_million=1, output_per_million=2),
            input_bound=1_000_000,
            output_bound=0,
            attempts=2,
        )
    with pytest.raises(ForbiddenError), database_sessions.begin() as session:
        billing.reserve(
            session,
            operation_id="model:2",
            run_id=run_id,
            user_id=alice.user_id,
            provider_id="p",
            model_id="m",
            pricing=ModelPricing(input_per_million=1, output_per_million=2),
            input_bound=1,
            output_bound=1,
            attempts=1,
        )
    with database_sessions.begin() as session:
        billing.settle(session, "model:1", input_tokens=500_000, output_tokens=0)
        billing.settle(session, "model:1", input_tokens=500_000, output_tokens=0)
    with database_sessions() as session:
        account = session.get(
            SpendingAccount, (alice.user_id, "USD", datetime.now(UTC).strftime("%Y-%m"))
        )
        assert account.spent == Decimal("0.5") and account.reserved == 0


def test_trigger_replay_has_one_run_and_one_committed_outbox_intent(database_sessions, actors):
    cipher = database_sessions.kw["info"]["dispatch_cipher"]
    _, alice, _ = actors
    ports = target_ports(cipher)
    with database_sessions.begin() as session:
        instance = create_instance(
            session,
            actor=alice,
            cipher=cipher,
            request=InstanceCreate(agent_id="private", request_key="trigger-instance"),
            load=lambda _: agent(),
        )
        row = triggers.save(
            session,
            actor=alice,
            cipher=cipher,
            ports=ports,
            expected_revision=0,
            spec=TriggerSpec(
                name="Hook", kind="webhook", target_kind="agent", target_id=instance.id
            ),
            secret="test-signing-secret-that-is-at-least-32-bytes",
        )
        first = triggers.fire(
            session, row=row, event_id="e1", event={"text": "hello"}, cipher=cipher, ports=ports
        )
        second = triggers.fire(
            session, row=row, event_id="e1", event={"text": "hello"}, cipher=cipher, ports=ports
        )
        assert first.id == second.id
        with pytest.raises(ConflictError):
            triggers.fire(
                session,
                row=row,
                event_id="e1",
                event={"text": "different"},
                cipher=cipher,
                ports=ports,
            )
        assert session.scalar(select(func.count()).select_from(ExecutionRun)) == 1
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 1
        dataset = evaluations.save_dataset(
            session,
            actor=alice,
            cipher=cipher,
            expected_version=0,
            spec=DatasetSpec(
                name="Regression", cases=[DatasetCase(id="one", input="hello", expected="hello")]
            ),
        )
        experiment = evaluations.create_experiment(
            session,
            actor=alice,
            cipher=cipher,
            ports=ports,
            evaluators=default_evaluators(),
            spec=ExperimentSpec(
                dataset_id=dataset.id,
                dataset_version=1,
                targets=[EvaluationTarget(kind="agent", instance_id=instance.id)],
                evaluators=[EvaluatorSpec(name="exact")],
                request_key="regression",
            ),
        )
        dispatch_evaluation(session, experiment.id)
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 2
