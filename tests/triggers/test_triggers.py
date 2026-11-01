import asyncio
import hashlib
import hmac
import time
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.triggers.api import TriggerDefinition
from opsmesh.modules.triggers.internal.service import PersistentTriggerService

SECRET = "offline-webhook-test-signing-key-only"


@pytest_asyncio.fixture
async def harness(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'triggers.db').as_posix()}")
    store = VersionedRecordStore(database, "triggers")
    await database.initialize()
    invocations, checked, denied, statuses = [], [], set(), {}

    async def authorize(actor, action, resource):
        checked.append((actor, action, resource))
        if (actor, action) in denied:
            raise OpsMeshError("FORBIDDEN", "Not permitted")

    async def resolve(reference):
        if reference not in {"helper", "helper@1"}:
            raise OpsMeshError("NOT_FOUND", "Agent not published")
        return AgentSpecification(id="helper", name="Helper")

    async def submit(invocation):
        invocations.append(invocation)
        await asyncio.sleep(0)
        run_id = f"run-{len(invocations)}"
        statuses[run_id] = "queued"
        return run_id

    async def resolve_run(run_id):
        return AgentRunView(
            id=run_id,
            revision=1,
            principal_id="owner",
            session_id="session",
            agent=await resolve("helper"),
            status=statuses[run_id],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    service = PersistentTriggerService(
        store, authorize, resolve, submit, lambda _: SECRET, run_resolver=resolve_run
    )
    yield service, invocations, checked, denied, statuses
    await database.close()


def definition(**changes):
    return TriggerDefinition(
        id="alert",
        principal_id="owner",
        agent_revision="helper",
        source="monitor",
        event_type="incident.opened",
        **changes,
    )


def signed(body, delivery_id="delivery-1", epoch=None):
    timestamp = str(int(time.time()) if epoch is None else epoch)
    signature = (
        "sha256="
        + hmac.new(
            SECRET.encode(), f"{timestamp}.{delivery_id}.".encode() + body, hashlib.sha256
        ).hexdigest()
    )
    return {"timestamp": timestamp, "delivery_id": delivery_id, "signature": signature}


async def test_authorized_configuration_and_versioned_lifecycle(harness):
    service, _, checked, denied, _ = harness
    denied.add(("manager", "triggers:impersonate"))
    with pytest.raises(OpsMeshError, match="Not permitted"):
        await service.create(definition(), actor_id="manager")
    denied.clear()
    created = await service.create(definition(), actor_id="manager")
    assert created.agent_revision == "helper@1"
    assert ("owner", "agent:run", "helper@1") in checked
    assert ("manager", "agent:run", "helper@1") in checked
    updated = await service.update(
        "alert", created.model_copy(update={"input": "triage"}), 1, actor_id="manager"
    )
    assert updated.revision == 2
    with pytest.raises(OpsMeshError, match="configuration was changed"):
        await service.set_enabled("alert", False, 1, actor_id="manager")
    disabled = await service.set_enabled("alert", False, 2, actor_id="manager")
    assert not disabled.enabled and disabled.revision == 3
    await service.delete("alert", 3, actor_id="manager")
    assert await service.list_triggers(actor_id="manager") == []
    with pytest.raises(OpsMeshError, match="does not exist"):
        await service.get("alert", actor_id="manager")


async def test_field_matching_and_input_mapping_submit_fixed_agent(harness):
    service, invocations, _, _, _ = harness
    await service.create(
        definition(
            filters={"payload.severity": "critical"},
            input_mapping={"service": "payload.service.name", "incident": "id"},
        ),
        actor_id="owner",
    )
    ignored = OpsMeshEvent(
        id="e-0", source="monitor", type="incident.opened", payload={"severity": "low"}
    )
    assert await service.dispatch_event(ignored, actor_id="owner") == []
    event = OpsMeshEvent(
        id="e-1",
        source="monitor",
        type="incident.opened",
        payload={
            "severity": "critical",
            "service": {"name": "database"},
        },
    )
    (delivered,) = await service.dispatch_event(event, actor_id="owner")
    assert delivered.status == "submitted" and delivered.run_id == "run-1"
    (invocation,) = invocations
    assert invocation.agent_revision == "helper@1"
    assert invocation.input == '{"service": "database", "incident": "e-1"}'
    assert invocation.session_policy == "per_event"
    assert invocation.idempotency_key.startswith("trigger:")


async def test_concurrent_delivery_is_submitted_once_and_payload_reuse_rejected(harness):
    service, invocations, _, _, _ = harness
    await service.create(definition(), actor_id="owner")
    event = OpsMeshEvent(
        id="same", source="monitor", type="incident.opened", payload={"title": "down"}
    )
    results = await asyncio.gather(
        *(service.dispatch_event(event, actor_id="owner") for _ in range(5))
    )
    assert len(invocations) == 1
    assert len({result[0].id for result in results}) == 1
    (again,) = await service.dispatch_event(event, actor_id="owner")
    assert again.status == "submitted" and len(invocations) == 1
    with pytest.raises(OpsMeshError, match="different event"):
        await service.dispatch_event(
            event.model_copy(update={"payload": {"title": "different"}}), actor_id="owner"
        )


async def test_execution_permissions_rechecked_for_initiator_and_configured_principal(harness):
    service, invocations, _, denied, _ = harness
    await service.create(definition(), actor_id="owner")
    event = OpsMeshEvent(source="monitor", type="incident.opened")
    denied.add(("caller", "triggers.execute"))
    with pytest.raises(OpsMeshError, match="Not permitted"):
        await service.dispatch_event(event, actor_id="caller")
    denied.clear()
    denied.add(("owner", "agent:run"))
    (delivery,) = await service.dispatch_event(event, actor_id="caller")
    assert delivery.status == "failed" and delivery.error.code == "FORBIDDEN"
    assert invocations == []


async def test_webhook_verifies_signature_timestamp_and_delivery_id(harness):
    service, invocations, checked, _, _ = harness
    await service.create(definition(webhook_secret_ref="env:TEST_TRIGGER_KEY"), actor_id="owner")
    assert ("owner", "secret:use", "env:TEST_TRIGGER_KEY") in checked
    body = b'{"severity":"critical"}'
    credentials = signed(body)
    with pytest.raises(OpsMeshError, match="authentication"):
        await service.ingest_webhook(
            "alert", body, **{**credentials, "signature": "sha256=" + "0" * 64}
        )
    with pytest.raises(OpsMeshError, match="authentication"):
        await service.ingest_webhook("alert", body, **{**credentials, "delivery_id": "tampered"})
    with pytest.raises(OpsMeshError, match="expired"):
        await service.ingest_webhook("alert", body, **signed(body, epoch=int(time.time()) - 600))
    with pytest.raises(OpsMeshError, match="authentication"):
        await service.ingest_webhook("alert", body, **{**credentials, "signature": "密" * 71})
    result = await service.ingest_webhook("alert", body, **credentials)
    replay = await service.ingest_webhook("alert", body, **credentials)
    assert result == replay and len(invocations) == 1
    assert SECRET not in str([r.data for r in await service.store.list()])


async def test_webhook_filter_miss_is_recorded_and_no_run_submitted(harness):
    service, invocations, _, _, _ = harness
    await service.create(
        definition(webhook_secret_ref="env:TEST_TRIGGER_KEY", filters={"payload.status": "down"}),
        actor_id="owner",
    )
    body = b'{"status":"up"}'
    result = await service.ingest_webhook("alert", body, **signed(body))
    assert result.status == "ignored" and result.reason == "filter_mismatch"
    assert result.finished_at is not None and invocations == []
    assert (await service.list_deliveries("alert", actor_id="owner")) == [result]


async def test_missing_input_mapping_is_durable_safe_failure(harness):
    service, invocations, _, _, _ = harness
    await service.create(definition(input_mapping={"need": "payload.missing"}), actor_id="owner")
    (result,) = await service.dispatch_event(
        OpsMeshEvent(source="monitor", type="incident.opened"), actor_id="owner"
    )
    assert result.status == "failed" and result.error.code == "VALIDATION_ERROR"
    assert invocations == []


async def test_real_run_projection_and_history_survives_trigger_deletion(harness):
    service, _, _, _, statuses = harness
    created = await service.create(definition(), actor_id="owner")
    (result,) = await service.dispatch_event(
        OpsMeshEvent(source="monitor", type="incident.opened"), actor_id="owner"
    )
    assert result.status == "submitted" and result.finished_at is None
    statuses[result.run_id] = "waiting_approval"
    assert await service.reconcile() == 1
    assert (await service.list_deliveries("alert", actor_id="owner"))[
        0
    ].status == "waiting_approval"
    statuses[result.run_id] = "completed"
    assert await service.reconcile() == 1
    await service.delete("alert", created.revision, actor_id="owner")
    (history,) = await service.list_deliveries("alert", actor_id="owner")
    assert history.status == "completed" and history.finished_at is not None


async def test_reconfiguration_does_not_replay_already_accepted_event(harness):
    service, invocations, _, _, _ = harness
    created = await service.create(definition(), actor_id="owner")
    event = OpsMeshEvent(id="event", source="monitor", type="incident.opened")
    (first,) = await service.dispatch_event(event, actor_id="owner")
    await service.update(
        "alert", created.model_copy(update={"input": "new settings"}), 1, actor_id="owner"
    )
    (repeated,) = await service.dispatch_event(event, actor_id="owner")
    assert repeated.id == first.id and repeated.trigger_revision == 1
    assert len(invocations) == 1


async def test_trigger_configuration_and_delivery_publish_safe_audit_facts(harness):
    service, _, _, _, _ = harness
    created = await service.create(
        definition(webhook_secret_ref="env:TEST_TRIGGER_KEY"), actor_id="owner"
    )
    await service.dispatch_event(
        OpsMeshEvent(source="monitor", type="incident.opened"), actor_id="owner"
    )
    await service.delete("alert", created.revision, actor_id="owner")
    facts = await service.store.database.pending_events()
    assert {f["type"] for f in facts} >= {
        "triggers.definition.created",
        "triggers.definition.deleted",
        "triggers.delivery.submitted",
    }
    assert all(f["actor_ref"] == "owner" for f in facts)
    assert "TEST_TRIGGER_KEY" not in str(facts) and SECRET not in str(facts)


async def test_disable_remains_available_when_execution_target_is_withdrawn(harness):
    service, _, _, denied, _ = harness
    created = await service.create(definition(), actor_id="owner")
    denied.add(("owner", "agent:run"))
    disabled = await service.set_enabled("alert", False, created.revision, actor_id="owner")
    assert disabled.enabled is False
    with pytest.raises(OpsMeshError, match="Not permitted"):
        await service.set_enabled("alert", True, disabled.revision, actor_id="owner")


@pytest.mark.parametrize("mode", ["ack_lost", "wrong_metadata", "explicit_conflict"])
async def test_submission_lookup_does_not_associate_unrelated_runs(harness, mode):
    service, _, _, _, _ = harness
    committed = []

    async def lost_ack(invocation):
        committed.append(
            AgentRunView(
                id="committed",
                revision=1,
                principal_id="owner",
                session_id="session",
                agent=AgentSpecification(id="helper", name="Helper"),
                published_agent_ref="helper@1",
                status="queued",
                metadata={
                    "trigger_id": invocation.trigger_id,
                    "trigger_delivery_id": invocation.delivery_id,
                    "event_id": invocation.event_id if mode != "wrong_metadata" else "unrelated",
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        if mode == "explicit_conflict":
            raise OpsMeshError("IDEMPOTENCY_CONFLICT", "Different submission", retryable=True)
        raise RuntimeError("Acknowledgment lost after commit")

    async def lookup(principal_id, key):
        return committed[0]

    service.engine.submitter = lost_ack
    service.engine.submission_resolver = lookup
    await service.create(definition(), actor_id="owner")
    (result,) = await service.dispatch_event(
        OpsMeshEvent(source="monitor", type="incident.opened"), actor_id="owner"
    )
    if mode == "ack_lost":
        assert result.status == "submitted" and result.run_id == "committed"
    else:
        assert result.status == "failed" and result.run_id is None
        assert result.error.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("body", [b"[]", b'{"n":NaN}', b"not-json"])
async def test_webhook_rejects_invalid_json_after_authentication(harness, body):
    service, _, _, _, _ = harness
    await service.create(definition(webhook_secret_ref="env:TEST_TRIGGER_KEY"), actor_id="owner")
    with pytest.raises(OpsMeshError, match="JSON object"):
        await service.ingest_webhook("alert", body, **signed(body))
