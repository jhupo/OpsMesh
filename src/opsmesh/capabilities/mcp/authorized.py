"""Product authorization and evidence around the official MCP protocol client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, cast
from uuid import UUID

from agents import RunContextWrapper
from agents.agent import AgentBase
from agents.mcp import MCPServer
from mcp.types import LOG_LEVEL_META_KEY, CallToolResult, GetPromptResult, ListPromptsResult, Tool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionRequest
from opsmesh.capabilities.mcp.execution.diagnostics import (
    DIAGNOSTIC_META_KEY,
    native_failure,
    normalize_failure,
    response_diagnostic,
)
from opsmesh.capabilities.mcp.execution.events import MCPExecutionNotifier, MCPToolCallRecorder
from opsmesh.capabilities.mcp.execution.policy import (
    MCPExecutionPolicy,
    resolve_mcp_execution_policy,
)
from opsmesh.capabilities.mcp.execution.validation import MCPExecutionValidator
from opsmesh.capabilities.mcp.models import MCPServer as MCPServerRecord
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.capabilities.mcp.policy import MCP_LIMIT_COUNTED_STATUSES
from opsmesh.capabilities.tools.contracts import ToolPermissionError
from opsmesh.orchestration.approvals.models import Approval, PendingToolInvocation
from opsmesh.orchestration.approvals.pending_tools import PendingToolInvocationService
from opsmesh.orchestration.approvals.policy import ApprovalPolicyEngine
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUS_VALUES
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import canonical_payload


class AuthorizedMCPServer(MCPServer):
    """Keep current tenant permissions, approval and call budgets at Activity admission.

    Protocol transport, pagination, tools and cancellation remain owned by the SDK.
    The wrapper adds only product authorization, durable call admission and audit evidence.
    """

    def __init__(
        self,
        create_server: Callable[[], MCPServer],
        *,
        sessions: Callable[[], AbstractContextManager[Session]],
        settings: Settings,
        workspace_id: UUID,
        run_id: UUID,
        server_id: UUID,
        agent_profile_id: UUID,
        capability_catalog_fingerprint: str,
    ) -> None:
        super().__init__(require_approval="never", failure_error_function=None)
        self._create_server = create_server
        self._inner: MCPServer | None = None
        self._sessions = sessions
        self._settings = settings
        self._workspace_id = workspace_id
        self._run_id = run_id
        self._server_id = server_id
        self._agent_profile_id = agent_profile_id
        self._catalog_fingerprint = capability_catalog_fingerprint

    @property
    def name(self) -> str:
        return f"mcp_{self._server_id}"

    async def connect(self) -> None:
        creation = asyncio.create_task(asyncio.to_thread(self._create_server))
        try:
            self._inner = await asyncio.shield(creation)
        except asyncio.CancelledError:
            self._inner = await creation
            await asyncio.shield(self.cleanup())
            raise
        try:
            await cast(Callable[[], Awaitable[None]], self._inner.connect)()
        except BaseException:
            await asyncio.shield(self.cleanup())
            raise

    async def cleanup(self) -> None:
        if self._inner is not None:
            await cast(Callable[[], Awaitable[None]], self._inner.cleanup)()
            self._inner = None

    async def list_tools(
        self, run_context: RunContextWrapper[Any] | None = None, agent: AgentBase | None = None
    ) -> list[Tool]:
        return await self._require_connected().list_tools(run_context, agent)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        call_id = (meta or {}).get("opsmesh_tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise PolicyDeniedError("MCP invocation requires the SDK call identity")
        request = MCPExecutionRequest(
            workspace_id=self._workspace_id,
            agent_run_id=self._run_id,
            agent_profile_id=self._agent_profile_id,
            capability_catalog_fingerprint=self._catalog_fingerprint,
            mcp_server_id=self._server_id,
            tool_name=tool_name,
            arguments=arguments or {},
            tool_call_id=call_id,
        )
        admission = asyncio.create_task(asyncio.to_thread(self._authorize_call, request))
        try:
            request, policy, log_id, invocation_id, snapshot = await asyncio.shield(admission)
        except asyncio.CancelledError:
            request, _, log_id, invocation_id, snapshot = await admission
            await asyncio.to_thread(
                self._complete,
                request,
                log_id,
                monotonic(),
                None,
                {
                    "code": "mcp_cancelled_before_call",
                    "message": "MCP call cancelled before dispatch",
                },
                invocation_id,
                snapshot,
            )
            raise
        started = monotonic()
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                response = await self._require_connected().call_tool(
                    tool_name, request.arguments, {**(meta or {}), LOG_LEVEL_META_KEY: "warning"}
                )
            payload = response.model_dump(mode="json", by_alias=True)
            if len(canonical_payload(payload).encode("utf-8")) > policy.max_output_bytes:
                raise PolicyDeniedError("MCP result exceeds its configured call budget")
        except BaseException as exc:
            await asyncio.shield(
                asyncio.to_thread(
                    self._complete,
                    request,
                    log_id,
                    started,
                    None,
                    native_failure(exc),
                    invocation_id,
                    snapshot,
                )
            )
            raise
        error: dict[str, object] | None = (
            normalize_failure(
                response_diagnostic(payload),
                code="mcp_tool_error",
                message="MCP tool returned an error",
            )
            if response.is_error
            else None
        )
        completion = asyncio.create_task(
            asyncio.to_thread(
                self._complete, request, log_id, started, payload, error, invocation_id, snapshot
            )
        )
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            await completion
            raise
        return response

    def _require_connected(self) -> MCPServer:
        if self._inner is None:
            raise ValueError("MCP client has not connected")
        return self._inner

    def _authorize_call(
        self, request: MCPExecutionRequest
    ) -> tuple[MCPExecutionRequest, MCPExecutionPolicy, UUID, UUID | None, dict[str, object]]:
        with self._sessions() as session:
            try:
                validated = MCPExecutionValidator(session, self._settings).validate(request)
            except ToolPermissionError:
                # A rejected call still owns durable tenant-scoped denial evidence.
                session.commit()
                raise
            if validated.run.status not in ACTIVE_RUN_STATUS_VALUES:
                raise PolicyDeniedError("MCP invocation belongs to an inactive run")
            request = validated.request
            policy = resolve_mcp_execution_policy(validated.snapshot, validated.descriptor)
            if len(canonical_payload(request.arguments).encode("utf-8")) > policy.max_input_bytes:
                raise PolicyDeniedError("MCP input exceeds its configured call budget")
            decision = ApprovalPolicyEngine(session, self._settings).evaluate_mcp_tool(
                workspace_id=request.workspace_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                allowlist_policy=validated.descriptor.policy,
                allowlist_risk_level=validated.descriptor.risk_level,
                requires_approval=validated.descriptor.requires_approval,
                context={
                    "agent_run_id": str(request.agent_run_id),
                    "mcp_server_id": str(self._server_id),
                },
            )
            approved = self._approved(session, request) if decision.required else None
            if decision.blocked or (decision.required and approved is None):
                raise PolicyDeniedError("MCP invocation has no current approval")
            self._admit(session, request, policy.max_calls_per_run, policy.max_calls_per_hour)
            log = MCPToolCallRecorder(session).record(
                request=request,
                server_id=self._server_id,
                status="running",
                response=None,
                error=None,
                snapshot=validated.snapshot,
                run=validated.run,
            )
            log_id = log.id
            invocation_id = approved.id if approved is not None else None
            if approved is not None:
                approved.status = "executing"
                approved.attempt_count += 1
                approved.execution_started_at = datetime.now(UTC)
                log.approval_id = approved.approval_id
            session.commit()
            return request, policy, log_id, invocation_id, validated.snapshot

    def _complete(
        self,
        request: MCPExecutionRequest,
        log_id: UUID,
        started: float,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        invocation_id: UUID | None,
        snapshot: dict[str, object],
    ) -> None:
        with self._sessions() as session:
            current = session.scalar(
                select(MCPToolCallLog)
                .where(
                    MCPToolCallLog.workspace_id == request.workspace_id,
                    MCPToolCallLog.id == log_id,
                )
                .with_for_update()
            )
            metadata = (current.response or {}).get("_meta") if current is not None else None
            notifications = (
                metadata.get(DIAGNOSTIC_META_KEY) if isinstance(metadata, dict) else None
            )
            if isinstance(notifications, dict) and notifications.get("notifications"):
                response = dict(response or {})
                raw_metadata = response.get("_meta")
                response_metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                existing = response_metadata.get(DIAGNOSTIC_META_KEY)
                private = dict(existing) if isinstance(existing, dict) else {}
                private["notifications"] = notifications["notifications"]
                response_metadata[DIAGNOSTIC_META_KEY] = private
                response["_meta"] = response_metadata
            log = MCPToolCallRecorder(session).record(
                request=request,
                server_id=self._server_id,
                status="failed" if error else "completed",
                response=response,
                error=error,
                latency_ms=int((monotonic() - started) * 1000),
                log_id=log_id,
            )
            run = session.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == request.workspace_id,
                    AgentRun.id == request.agent_run_id,
                )
            )
            if run is None:
                raise PolicyDeniedError("MCP completion run is unavailable")
            notifier = MCPExecutionNotifier(session)
            notifier.append_run_event(
                run=run,
                event_type=f"mcp.tool.{log.status}",
                message="Native MCP tool execution completed",
                metadata={
                    "mcp_tool_call_log_id": str(log.id),
                    "mcp_server_id": str(self._server_id),
                    "tool_name": request.tool_name,
                    "tool_call_id": request.tool_call_id,
                    "status": log.status,
                    "latency_ms": log.latency_ms,
                    "error_code": log.error_code,
                },
            )
            notifier.append_execution_audit(
                run=run,
                request=request,
                server_id=self._server_id,
                log=log,
                action=f"mcp.tool.{log.status}",
                snapshot=snapshot,
            )
            if invocation_id is not None:
                invocation = session.scalar(
                    select(PendingToolInvocation)
                    .where(
                        PendingToolInvocation.workspace_id == request.workspace_id,
                        PendingToolInvocation.agent_run_id == request.agent_run_id,
                        PendingToolInvocation.id == invocation_id,
                        PendingToolInvocation.status == "executing",
                    )
                    .with_for_update()
                )
                if invocation is None:
                    raise PolicyDeniedError("MCP completion has no claimed approval")
                PendingToolInvocationService(session, self._encryption()).complete_execution(
                    invocation,
                    {"status": log.status, "response": response, "error": error},
                )
            session.commit()

    def _approved(
        self, session: Session, request: MCPExecutionRequest
    ) -> PendingToolInvocation | None:
        invocation = session.scalar(
            select(PendingToolInvocation)
            .join(Approval, Approval.id == PendingToolInvocation.approval_id)
            .where(
                PendingToolInvocation.workspace_id == request.workspace_id,
                PendingToolInvocation.agent_run_id == request.agent_run_id,
                PendingToolInvocation.tool_call_id == request.tool_call_id,
                PendingToolInvocation.tool_name == request.tool_name,
                PendingToolInvocation.status == "approved",
                Approval.workspace_id == request.workspace_id,
                Approval.status == "approved",
            )
            .with_for_update()
        )
        if invocation is None:
            return None
        if (
            self._encryption().decrypt_payload(
                invocation.encrypted_arguments, key_id=invocation.encryption_key_id
            )
            != request.arguments
        ):
            return None
        return invocation

    def _encryption(self) -> SecretEncryptionService:
        return SecretEncryptionService(
            secret=self._settings.credential_encryption_secret,
            key_id=self._settings.credential_encryption_key_id,
            previous_secrets=self._settings.credential_encryption_previous_secrets,
        )

    def _admit(
        self,
        session: Session,
        request: MCPExecutionRequest,
        run_limit: int | None,
        hour_limit: int | None,
    ) -> None:
        session.scalar(
            select(MCPServerRecord)
            .where(
                MCPServerRecord.workspace_id == request.workspace_id,
                MCPServerRecord.id == self._server_id,
            )
            .with_for_update()
        )
        if (
            session.scalar(
                select(MCPToolCallLog.id).where(
                    MCPToolCallLog.workspace_id == request.workspace_id,
                    MCPToolCallLog.agent_run_id == request.agent_run_id,
                    MCPToolCallLog.tool_call_id == request.tool_call_id,
                )
            )
            is not None
        ):
            raise PolicyDeniedError(
                "MCP call was already admitted; its outcome must not be replayed"
            )
        base = (
            select(func.count())
            .select_from(MCPToolCallLog)
            .where(
                MCPToolCallLog.workspace_id == request.workspace_id,
                MCPToolCallLog.mcp_server_id == self._server_id,
                MCPToolCallLog.tool_name == request.tool_name,
                MCPToolCallLog.status.in_(MCP_LIMIT_COUNTED_STATUSES),
            )
        )
        if (
            run_limit is not None
            and int(
                session.scalar(base.where(MCPToolCallLog.agent_run_id == request.agent_run_id)) or 0
            )
            >= run_limit
        ):
            raise PolicyDeniedError("MCP run call budget is exhausted")
        if (
            hour_limit is not None
            and int(
                session.scalar(
                    base.where(MCPToolCallLog.created_at >= datetime.now(UTC) - timedelta(hours=1))
                )
                or 0
            )
            >= hour_limit
        ):
            raise PolicyDeniedError("MCP hourly call budget is exhausted")

    async def list_prompts(self) -> ListPromptsResult:
        return await self._require_connected().list_prompts()

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        return await self._require_connected().get_prompt(name, arguments)
