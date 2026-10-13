"""Relay the official Temporal stream into tenant-scoped durable product events."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from uuid import NAMESPACE_URL, uuid5

from agents.items import TResponseStreamEvent
from agents.stream_events import RawResponsesStreamEvent
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from opsmesh.agents.execution.contracts import AgentStreamEvent
from opsmesh.agents.execution.providers.openai.contracts import (
    AgentExecutionDefinition,
    AgentInvocationDefinition,
)
from opsmesh.agents.execution.providers.openai.results import runtime_stream_event_from_sdk_item
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.runs.events import RunEventWriter
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.event_store import TaskEventStore
from opsmesh.orchestration.tasks.models import TaskStep
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.security.redaction import redact_sensitive_payload

MODEL_EVENT_TOPIC = "opsmesh.agent.model"
AGENT_EVENT_TOPIC = "opsmesh.agent.events"


class AgentStreamRelayRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    from_offset: int = 0
    allow_live_text: bool = True


def allows_live_text(definition: AgentExecutionDefinition | AgentInvocationDefinition) -> bool:
    """Output validation must finish before any member's unvalidated text is public."""
    if definition.output_schema is not None or (
        definition.guardrails is not None and definition.guardrails.output
    ):
        return False
    targets = (
        (*definition.handoffs, *definition.agent_tools)
        if isinstance(definition, AgentExecutionDefinition)
        else (*definition.nested_handoffs, *definition.nested_tools)
    )
    return all(allows_live_text(target) for target in targets)


def public_stream_event(
    event: AgentStreamEvent, *, allow_live_text: bool
) -> AgentStreamEvent | None:
    if not allow_live_text and (
        event.delta is not None or event.event_type.startswith(("output.", "model."))
    ):
        return None
    return event


class AgentStreamActivities:
    """Stream retries replay offsets; PostgreSQL identities prevent duplicate evidence."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    @activity.defn(name="opsmesh.agent.relay_stream")
    async def relay(self, relay: AgentStreamRelayRequest) -> int:
        stream = WorkflowStreamClient.from_within_activity()

        async def heartbeat() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(5)

        watcher = asyncio.create_task(heartbeat())
        try:
            async for item in stream.subscribe(
                [MODEL_EVENT_TOPIC, AGENT_EVENT_TOPIC], from_offset=relay.from_offset
            ):
                if item.topic == MODEL_EVENT_TOPIC:
                    native: TResponseStreamEvent = TypeAdapter(
                        TResponseStreamEvent
                    ).validate_python(item.data)
                    event = runtime_stream_event_from_sdk_item(
                        RawResponsesStreamEvent(data=native), sequence=item.offset + 1
                    )
                else:
                    event = TypeAdapter(AgentStreamEvent).validate_python(item.data)
                if event is not None:
                    event = public_stream_event(event, allow_live_text=relay.allow_live_text)
                if event is not None:
                    await asyncio.to_thread(self._persist, relay.request, item.offset, event)
                    activity.heartbeat(item.offset + 1)
                    if item.topic == AGENT_EVENT_TOPIC and event.is_terminal:
                        return item.offset + 1
            raise RuntimeError("Agent stream closed before its terminal event was persisted")
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    def _persist(self, request: ExecutionRequest, offset: int, event: AgentStreamEvent) -> None:
        from opsmesh.orchestration.runs.tree_lock import lock_run_execution_tree

        event_id = uuid5(NAMESPACE_URL, f"{activity.info().workflow_id}:stream:{offset}")
        with self._sessions() as session:
            lock_run_execution_tree(session, request.workspace_id, request.resource_id)
            run = session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == request.workspace_id,
                    AgentRun.id == request.resource_id,
                )
                .with_for_update()
            )
            if run is None:
                raise ValueError("Agent stream target does not belong to the workspace")
            ExecutionIdentityService(session).for_run(request.workspace_id, request.resource_id)
            if session.get(RunEvent, event_id) is not None:
                return
            work_package = (
                session.scalar(
                    select(TaskStep.work_package_id).where(
                        TaskStep.workspace_id == run.workspace_id,
                        TaskStep.task_id == run.task_id,
                        TaskStep.id == run.task_step_id,
                    )
                )
                if run.task_step_id is not None
                else None
            )
            payload = redact_sensitive_payload(
                {
                    "agent_run_id": str(run.id),
                    "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
                    "work_package_id": work_package,
                    "runtime_stream_event": {
                        "event_type": event.event_type,
                        "provider_sequence": offset + 1,
                        "payload": event.payload,
                        "delta": event.delta,
                        "is_terminal": event.is_terminal,
                    },
                }
            )
            RunEventWriter(session).append(
                workspace_id=run.workspace_id,
                run_id=run.id,
                event_type=f"agent.stream.{event.event_type}",
                message="Agent SDK stream event",
                metadata=payload,
                event_id=event_id,
            )
            if run.task_id is not None:
                TaskEventStore(session).append(
                    workspace_id=run.workspace_id,
                    task_id=run.task_id,
                    event_id=event_id,
                    event_type=f"agent.stream.{event.event_type}",
                    payload=payload,
                )
            session.commit()
