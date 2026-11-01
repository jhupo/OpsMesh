"""Shared platform operations invoked by HTTP, bots, schedules and plugins."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, MessageEvent, OpsMeshEvent, TimerEvent
from opsmesh.framework.message import DispatchResult, handle_event
from opsmesh.infrastructure.telemetry.tracing import current_trace_carrier
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.configuration.api import PlatformConfiguration
from opsmesh.modules.runtime.api import (
    AgentRuntime,
    AgentRunView,
    RunOverrides,
    SubmitRunRequest,
    resolve_run_agent,
)
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.workspaces.api import WorkspaceService

from .authorization import PlatformAuthorization
from .conversations.validation import validate_conversation_agent
from .sandbox_bridge import SandboxWorkspaceBridge
from .waiting import wait_for_run


class ApplicationUseCases:
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        agents: AgentDefinitionCatalog,
        configuration: PlatformConfiguration,
        runtime: AgentRuntime,
        sessions: Conversations,
        workspaces: WorkspaceService,
        authorization: PlatformAuthorization,
        sandbox: SandboxWorkspaceBridge,
        is_started: Callable[[], bool],
        poll_interval: float,
    ) -> None:
        self.accounts = accounts
        self.agents = agents
        self.configuration = configuration
        self.runtime = runtime
        self.sessions = sessions
        self.workspaces = workspaces
        self.authorization = authorization
        self.sandbox = sandbox
        self.is_started = is_started
        self.poll_interval = poll_interval

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
        principal = await self.accounts.get_principal(principal_id)
        session = await self.sessions.get(session_id)
        await self.accounts.authorize(
            principal, "run:submit", session_id, owner_id=session.owner_id
        )
        specification = await self.agents.get_revision(agent_ref)
        await self.accounts.authorize(principal, "agent:run", specification.ref)
        if session.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "Conversation is closed")
        policy = await self.configuration.admission_policy()
        text = input if isinstance(input, str) else json.dumps(input, ensure_ascii=False)
        if len(text) > policy.max_input_characters:
            raise OpsMeshError("INPUT_TOO_LARGE", "Input exceeds the configured limit")
        overrides = overrides or RunOverrides()
        effective = resolve_run_agent(specification, overrides)
        if (metadata or {}).get("conversation_operation"):
            validate_conversation_agent(effective)
        sandbox = effective.sandbox
        source_selected = bool(sandbox and (sandbox.snapshot_ref or sandbox.session_ref))
        workspace = effective.workspace_id or (None if source_selected else session.workspace_id)
        if workspace:
            await self.authorization.workspace(principal_id, workspace, write=True)
        if sandbox and sandbox.snapshot_ref:
            source = await self.workspaces.get_snapshot(sandbox.snapshot_ref)
            await self.authorization.workspace(principal_id, source.workspace_id, write=True)
        if sandbox and sandbox.session_ref:
            source_workspace = await self.sandbox.authorize_session_source(principal_id, sandbox)
            await self.authorization.workspace(principal_id, source_workspace, write=True)
        run = await self.runtime.submit(
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
            await self.sessions.append(
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

    async def execute_memory_run(self, run_id: str) -> AgentRunView:
        if not self.is_started():
            return await self.runtime.execute(run_id)
        try:
            return await wait_for_run(
                lambda: self.runtime.get_run(run_id), interval=self.poll_interval
            )
        except asyncio.CancelledError:
            await self.runtime.cancel(run_id)
            raise

    async def dispatch(self, context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        managed = context.values.get("bot_dispatch_mode") == "managed"
        result = await handle_event(context, raise_on_error=True)
        if managed and not result.stopped:
            raise OpsMeshError(
                "BOT_HANDLER_UNAVAILABLE",
                "This conversation requires its managed event handler before messages can run",
            )
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
            await self.sessions.get(context.values["session_id"])
            if context.values.get("session_id")
            else await self.sessions.get_or_open(key, principal_id, agent_ref)
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
        run = await self.submit_run(
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
