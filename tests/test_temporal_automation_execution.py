"""Native Temporal signals and child delivery with offline business Activities.

This verifies workflow waiting and resumption, not PostgreSQL product admission.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Schedule, ScheduleActionStartWorkflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.orchestration.automations.execution_contracts import ReplyPreparation
from opsmesh.orchestration.automations.workflows import (
    AutomationEventWorkflow,
    ReplyDeliveryWorkflow,
)
from opsmesh.orchestration.scheduling.contracts import ScheduleOccurrence
from opsmesh.orchestration.scheduling.specs import build_schedule_spec
from opsmesh.orchestration.scheduling.workflows import ScheduleTickWorkflow
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest, WorkflowSignal
from opsmesh.runtime.temporal.workflows import WebhookDeliveryWorkflow

pytestmark = pytest.mark.skipif(
    os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Temporal integration opt-in required"
)


@pytest.mark.parametrize("initial_delivery_status", ["delivered", "sending", "unknown"])
def test_entry_and_reply_wait_for_signals_and_external_receipts(
    initial_delivery_status: str,
) -> None:
    async def verify():
        workspace_id, event_id, delivery_id = uuid4(), uuid4(), uuid4()
        entry_seen, reply_seen = asyncio.Event(), asyncio.Event()
        delivery_seen = asyncio.Event()
        dispatches, preparations, deliveries = [], [], []

        @activity.defn(name="opsmesh.automation.dispatch")
        async def dispatch(request: ExecutionRequest) -> str:
            dispatches.append(request.resource_id)
            entry_seen.set()
            return "pending" if len(dispatches) == 1 else "dispatched"

        @activity.defn(name="opsmesh.automation.prepare_reply")
        async def prepare(request: ExecutionRequest) -> ReplyPreparation:
            preparations.append(request.resource_id)
            reply_seen.set()
            return ReplyPreparation(
                complete=False, delivery_id=delivery_id if len(preparations) > 1 else None
            )

        @activity.defn(name="opsmesh.automation.settle_reply")
        async def settle(request: ExecutionRequest) -> bool:
            return True

        @activity.defn(name="opsmesh.channel.send")
        async def deliver(request: ExecutionRequest) -> str:
            deliveries.append(request.resource_id)
            delivery_seen.set()
            return initial_delivery_status if len(deliveries) == 1 else "delivered"

        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
        try:
            async with Worker(
                env.client,
                task_queue="automation-test",
                workflows=[AutomationEventWorkflow, ReplyDeliveryWorkflow, WebhookDeliveryWorkflow],
                activities=[dispatch, prepare, settle, deliver],
            ):
                entry = ExecutionRequest(
                    execution_id=event_id,
                    workspace_id=workspace_id,
                    resource_id=event_id,
                    kind=ExecutionKind.ENTRY_EVENT,
                )
                handle = await env.client.start_workflow(
                    AutomationEventWorkflow.run,
                    entry,
                    id=entry.workflow_id,
                    task_queue="automation-test",
                )
                await asyncio.wait_for(entry_seen.wait(), timeout=15)
                await handle.signal("wake", WorkflowSignal(resource_id=event_id))
                await asyncio.wait_for(handle.result(), timeout=15)
                assert dispatches == [event_id, event_id]
                reply = entry.model_copy(update={"kind": ExecutionKind.REPLY_DELIVERY})
                handle = await env.client.start_workflow(
                    ReplyDeliveryWorkflow.run,
                    reply,
                    id=reply.workflow_id,
                    task_queue="automation-test",
                )
                await asyncio.wait_for(reply_seen.wait(), timeout=15)
                assert deliveries == []
                await handle.signal("wake", WorkflowSignal(resource_id=event_id))
                if initial_delivery_status != "delivered":
                    await asyncio.wait_for(delivery_seen.wait(), timeout=15)
                    assert deliveries == [delivery_id]
                    await handle.signal("wake", WorkflowSignal(resource_id=event_id))
                await asyncio.wait_for(handle.result(), timeout=15)
                assert deliveries == [delivery_id] * (
                    1 if initial_delivery_status == "delivered" else 2
                )
        finally:
            await env.shutdown()

    asyncio.run(verify())


def test_native_schedule_starts_occurrence_workflow_with_durable_identity() -> None:
    async def verify():
        recorded = []
        occurred = asyncio.Event()

        @activity.defn(name="opsmesh.schedule.materialize")
        async def materialize(occurrence: ScheduleOccurrence) -> bool:
            recorded.append(occurrence)
            occurred.set()
            return True

        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
        handle = None
        try:
            async with Worker(
                env.client,
                task_queue="schedule-test",
                workflows=[ScheduleTickWorkflow],
                activities=[materialize],
            ):
                request = ExecutionRequest(
                    workspace_id=uuid4(),
                    resource_id=uuid4(),
                    kind=ExecutionKind.SCHEDULE_TICK,
                    parameters={"owner_kind": "scheduled_job"},
                )
                schedule_id = f"schedule-{uuid4()}"
                run_at = datetime.now(UTC) + timedelta(seconds=2)
                handle = await env.client.create_schedule(
                    schedule_id,
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            ScheduleTickWorkflow.run,
                            request,
                            id=schedule_id,
                            task_queue="schedule-test",
                        ),
                        spec=build_schedule_spec("one_shot", {"run_at": run_at.isoformat()}),
                    ),
                )
                await asyncio.wait_for(occurred.wait(), timeout=15)
                assert len(recorded) == 1
                assert recorded[0].request == request
                assert recorded[0].occurrence_id.startswith(schedule_id)
                assert recorded[0].started_at.tzinfo is not None
        finally:
            if handle is not None:
                await handle.delete()
            await env.shutdown()

    asyncio.run(verify())
