"""Shared platform operations invoked by HTTP, bots, schedules and plugins."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, MessageEvent, OpsMeshEvent, TimerEvent
from opsmesh.framework.message import DispatchResult, handle_event
from opsmesh.infrastructure.telemetry.tracing import current_trace_carrier
from opsmesh.modules.memory.api import MemoryJob
from opsmesh.modules.runtime.api import (
    TERMINAL_STATES,
    AgentRunView,
    RunOverrides,
    SubmitRunRequest,
    resolve_run_agent,
)
from opsmesh.modules.workspaces.api import WorkspaceDescriptor

from .ports import ApplicationHost


class ApplicationUseCases:
    def __init__(self, host: ApplicationHost) -> None:
        self.host = host
        self.services = host.services
        self.settings = host.settings

    async def create_workspace(self, principal_id: str, workspace_id: str) -> WorkspaceDescriptor:
        return await self.services.workspace_management.create(workspace_id, actor_id=principal_id)

    async def authorize_workspace(
        self, principal_id: str, workspace_id: str, *, write: bool = False
    ) -> None:
        await self.services.authorization.workspace(principal_id, workspace_id, write=write)

    async def restore_workspace(
        self, *, principal_id: str, snapshot_id: str, target_workspace_id: str
    ) -> WorkspaceDescriptor:
        return await self.services.workspace_management.restore(
            snapshot_id, target_workspace_id, actor_id=principal_id
        )

    async def submit_run(
        self,
        *,
        principal_id: str,
        session_id: str,
        agent_ref: str,
        input: str | list[dict[str, Any]],
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        overrides: RunOverrides | None = None,
    ) -> AgentRunView:
        services = self.services
        principal = await services.accounts.get_principal(principal_id)
        session = await services.sessions.get(session_id)
        await services.accounts.authorize(
            principal, "run:submit", session_id, owner_id=session.owner_id
        )
        specification = await services.agents.get_revision(agent_ref)
        await services.accounts.authorize(principal, "agent:run", specification.ref)
        if session.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "Conversation is closed")
        policy = await services.configuration.admission_policy()
        text = input if isinstance(input, str) else json.dumps(input, ensure_ascii=False)
        if len(text) > policy.max_input_characters:
            raise OpsMeshError("INPUT_TOO_LARGE", "Input exceeds the configured limit")
        overrides = overrides or RunOverrides()
        effective = resolve_run_agent(specification, overrides)
        sandbox = effective.sandbox
        source_selected = bool(sandbox and (sandbox.snapshot_ref or sandbox.session_ref))
        workspace = effective.workspace_id or (None if source_selected else session.workspace_id)
        if workspace:
            await self.host.authorize_workspace(principal_id, workspace, write=True)
        if sandbox and sandbox.snapshot_ref:
            source = await services.workspaces.get_snapshot(sandbox.snapshot_ref)
            await self.host.authorize_workspace(principal_id, source.workspace_id, write=True)
        if sandbox and sandbox.session_ref:
            source_workspace = await services.sandbox_bridge.authorize_session_source(
                principal_id, sandbox
            )
            await self.host.authorize_workspace(principal_id, source_workspace, write=True)
        run = await services.runtime.submit(
            SubmitRunRequest(
                principal_id=principal_id,
                session_id=session_id,
                agent=specification,
                input=input,
                idempotency_key=idempotency_key,
                overrides=overrides,
                metadata={
                    **(metadata or {}),
                    "workspace_id": workspace,
                    "trace_context": current_trace_carrier(),
                },
            )
        )
        try:
            await services.sessions.append(
                session_id,
                "user",
                text,
                message_id=f"{run.id}:user",
                run_id=run.id,
                created_at=run.created_at,
            )
        except Exception:
            # The Run and its queued Outbox fact are already durable. The coordinator
            # rebuilds the display-history projection from that fact with the same ID.
            logging.getLogger(__name__).exception("Submitted Run history projection deferred")
        return run

    async def resume_run(
        self, *, principal_id: str, run_id: str, expected_revision: int
    ) -> AgentRunView:
        run = await self.services.runtime.get_run(run_id)
        principal = await self.services.accounts.get_principal(principal_id)
        await self.services.accounts.authorize(
            principal, "run:submit", run_id, owner_id=run.principal_id
        )
        resumed = await self.services.runtime.resume(run_id, expected_revision)
        return resumed

    async def request_memory_generation(
        self, *, principal_id: str, namespace_id: str, idempotency_key: str
    ) -> MemoryJob:
        return await self.services.memory.request_generation(
            namespace_id, principal_id=principal_id, idempotency_key=idempotency_key
        )

    async def execute_memory_run(self, run_id: str) -> AgentRunView:
        if not self.host.is_started:
            return await self.services.runtime.execute(run_id)
        try:
            while True:
                run = await self.services.runtime.get_run(run_id)
                if run.status in TERMINAL_STATES or run.status == "waiting_approval":
                    return run
                await asyncio.sleep(self.settings.poll_interval_seconds)
        except asyncio.CancelledError:
            await self.services.runtime.cancel(run_id)
            raise

    async def dispatch(self, context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        result = await handle_event(context, raise_on_error=True)
        agent_ref = context.values.get("agent_revision")
        principal_id = context.values.get("principal_id")
        if result.stopped or not agent_ref or not principal_id:
            return result
        event = context.event
        if isinstance(event, MessageEvent):
            key, text = event.get_session_id(), event.get_plaintext()
        elif isinstance(event, TimerEvent):
            key = f"schedule:{event.job_id}"
            if context.values.get("session_policy", "per_fire") == "per_fire":
                key += f":{context.values.get('occurrence_id', event.id)}"
            text = json.dumps(event.payload, ensure_ascii=False)
        else:
            return result
        session = (
            await self.services.sessions.get(context.values["session_id"])
            if context.values.get("session_id")
            else await self.services.sessions.get_or_open(key, principal_id, agent_ref)
        )
        metadata: dict[str, Any] = {"event_id": event.id}
        for field in ("schedule_id", "occurrence_id", "notify_on_failure", "notify_on_approval"):
            if field in context.values:
                metadata[field] = context.values[field]
        if context.reply_target:
            metadata["reply_target"] = context.reply_target.model_dump(mode="json")
        run_input = context.values.get("input")
        if run_input is None:
            run_input = text
        run = await self.host.submit_run(
            principal_id=principal_id,
            session_id=session.id,
            agent_ref=agent_ref,
            input=run_input,
            idempotency_key=event.id,
            metadata=metadata,
            overrides=RunOverrides.model_validate(context.values.get("run_overrides", {})),
        )
        context.values["run_id"] = run.id
        result.matched += 1
        return result
