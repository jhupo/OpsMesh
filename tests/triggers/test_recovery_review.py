"""Independent regression checks for accepted inputs across delivery lease recovery."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent

from .test_triggers import definition
from .test_triggers import harness as harness


async def test_event_authorization_is_checked_before_any_matching_trigger_submits(harness):
    service, invocations, _checked, _denied, _statuses = harness
    await service.create(definition().model_copy(update={"id": "a-allowed"}), actor_id="owner")
    await service.create(definition().model_copy(update={"id": "b-blocked"}), actor_id="owner")
    original_authorize = service.authorize

    async def scoped_authorize(actor_id, action, resource):
        if actor_id == "caller" and action == "triggers.execute" and resource == "b-blocked":
            raise OpsMeshError("FORBIDDEN", "This trigger is not delegated to the caller")
        await original_authorize(actor_id, action, resource)

    service.authorize = scoped_authorize
    with pytest.raises(OpsMeshError) as denied:
        await service.dispatch_event(
            OpsMeshEvent(id="batch-event", source="monitor", type="incident.opened"),
            actor_id="caller",
        )
    assert denied.value.code == "FORBIDDEN"
    assert invocations == []


async def test_recovery_preserves_first_timestamp_mapping_after_commit_and_cancellation(harness):
    service, _invocations, _checked, _denied, _statuses = harness
    await service.create(definition(input_mapping={"time": "occurred_at"}), actor_id="owner")
    accepted_inputs = {}
    attempts = 0

    async def submit(invocation):
        nonlocal attempts
        attempts += 1
        prior = accepted_inputs.setdefault(invocation.idempotency_key, invocation.input)
        if prior != invocation.input:
            raise OpsMeshError("CONFLICT", "Idempotency key used with different input")
        if attempts == 1:
            # Model the process dying after the Run commit but before delivery acknowledgment.
            raise asyncio.CancelledError
        return "already-committed-run"

    service.engine.submitter = submit
    initial = OpsMeshEvent(
        id="stable-id",
        source="monitor",
        type="incident.opened",
        occurred_at=datetime.now(UTC),
        payload={"incident": "outage"},
    )
    with pytest.raises(asyncio.CancelledError):
        await service.dispatch_event(initial, actor_id="owner")
    row = next(row for row in await service.store.list() if row.data.get("kind") == "delivery")
    await service.store.update(
        row.id,
        {**row.data, "lease_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        row.revision,
    )
    retry = initial.model_copy(update={"occurred_at": initial.occurred_at + timedelta(seconds=1)})
    (delivery,) = await service.dispatch_event(retry, actor_id="owner")
    assert delivery.status == "submitted", delivery.error
    assert delivery.run_id == "already-committed-run"
    assert len(accepted_inputs) == 1


async def test_rule_filter_update_does_not_strand_an_already_accepted_delivery(harness):
    service, _invocations, _checked, _denied, _statuses = harness
    created = await service.create(definition(filters={"payload.kind": "old"}), actor_id="owner")
    attempts = 0

    async def submit(invocation):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError
        return "accepted-before-rule-update"

    service.engine.submitter = submit
    event = OpsMeshEvent(
        id="before-rule-update", source="monitor", type="incident.opened", payload={"kind": "old"}
    )
    with pytest.raises(asyncio.CancelledError):
        await service.dispatch_event(event, actor_id="owner")
    row = next(row for row in await service.store.list() if row.data.get("kind") == "delivery")
    await service.store.update(
        row.id,
        {**row.data, "lease_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        row.revision,
    )
    await service.update(
        "alert",
        created.model_copy(update={"filters": {"payload.kind": "new"}}),
        1,
        actor_id="owner",
    )
    recovered = await service.dispatch_event(event, actor_id="owner")
    assert len(recovered) == 1
    assert recovered[0].status == "submitted"
    assert recovered[0].trigger_revision == 1
    assert recovered[0].run_id == "accepted-before-rule-update"
