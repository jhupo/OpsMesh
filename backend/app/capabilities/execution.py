from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from opentelemetry.trace import SpanKind
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.approvals.policy import ApprovalPolicyEngine
from backend.app.capabilities.mcp.adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp.policy import MCP_LIMIT_COUNTED_STATUSES
from backend.app.capabilities.mcp.types import (
    McpExecutionRequest,
    McpExecutionResult,
)
from backend.app.capabilities.mcp_execution_approvals import McpToolApprovalRequester
from backend.app.capabilities.mcp_execution_blocking import McpExecutionBlocker
from backend.app.capabilities.mcp_execution_context import snapshot_audit_metadata
from backend.app.capabilities.mcp_execution_invocation import McpToolInvoker
from backend.app.capabilities.mcp_execution_policy import resolve_mcp_execution_policy
from backend.app.capabilities.mcp_execution_validation import McpExecutionValidator
from backend.app.capabilities.models import McpToolCallLog
from backend.app.core.config import Settings, get_settings
from backend.app.core.trace_context import current_trace_context, telemetry_span


class McpToolExecutionService:
    def __init__(
        self,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._adapter_or_resolver = adapter
        self._settings = settings or get_settings()

    async def execute(self, request: McpExecutionRequest) -> McpExecutionResult:
        with telemetry_span(
            "opsmesh.mcp.tool.execute",
            parent=current_trace_context(),
            kind=SpanKind.CLIENT,
            attributes={
                "opsmesh.workspace.id": str(request.workspace_id),
                "opsmesh.run.id": str(request.agent_run_id),
                "opsmesh.tool.name": request.tool_name,
            },
        ):
            return await self._execute(request)

    async def _execute(self, request: McpExecutionRequest) -> McpExecutionResult:
        validated = McpExecutionValidator(self._session, self._settings).validate(request)
        request = validated.request
        run = validated.run
        snapshot = validated.snapshot
        allow = validated.allow
        server = validated.server
        policy = resolve_mcp_execution_policy(snapshot, allow)
        self._enforce_call_limit(
            request,
            server_id=server.id,
            max_calls_per_run=policy.max_calls_per_run,
            max_calls_per_hour=policy.max_calls_per_hour,
        )
        execution_review = ApprovalPolicyEngine(
            self._session,
            self._settings,
        ).evaluate_mcp_tool(
            workspace_id=request.workspace_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            allowlist_policy=allow.policy,
            allowlist_risk_level=allow.risk_level,
            requires_approval=allow.requires_approval,
            context={
                "agent_run_id": str(run.id),
                "task_id": str(run.task_id) if run.task_id is not None else None,
                "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run.agent_profile_id is not None
                else None,
                "mcp_server_id": str(server.id),
                **snapshot_audit_metadata(snapshot),
            },
        )
        if execution_review.blocked:
            reason = (
                "mcp_high_risk_tool_globally_disabled"
                if "platform.high_risk_tool.blocked" in execution_review.reasons
                else "mcp_tool_policy_denied"
            )
            self._block(
                request,
                reason,
                mcp_server_id=server.id,
            )
        if execution_review.required and not request.approval_granted:
            review_reason = (
                "mcp_tool_requires_approval"
                if allow.requires_approval
                else "mcp_tool_execution_review_requires_approval"
            )
            return self._approval_requester().request(
                request,
                run,
                allow,
                server,
                reason=review_reason,
                execution_review=execution_review,
            )

        return await McpToolInvoker(self._session, self._adapter_or_resolver).invoke(
            request=request,
            run=run,
            server=server,
            snapshot=snapshot,
            policy=policy,
        )

    def _enforce_call_limit(
        self,
        request: McpExecutionRequest,
        *,
        server_id: UUID,
        max_calls_per_run: int | None,
        max_calls_per_hour: int | None,
    ) -> None:
        if max_calls_per_run is None and max_calls_per_hour is None:
            return
        if max_calls_per_run is not None:
            current_run_count = self._session.scalar(
                select(func.count())
                .select_from(McpToolCallLog)
                .where(
                    McpToolCallLog.workspace_id == request.workspace_id,
                    McpToolCallLog.agent_run_id == request.agent_run_id,
                    McpToolCallLog.mcp_server_id == server_id,
                    McpToolCallLog.tool_name == request.tool_name,
                    McpToolCallLog.status.in_(MCP_LIMIT_COUNTED_STATUSES),
                )
            )
            if int(current_run_count or 0) >= max_calls_per_run:
                self._block(
                    request,
                    "mcp_tool_run_call_limit_exceeded",
                    mcp_server_id=server_id,
                )
        if max_calls_per_hour is not None:
            window_started_at = datetime.now(UTC) - timedelta(hours=1)
            current_hour_count = self._session.scalar(
                select(func.count())
                .select_from(McpToolCallLog)
                .where(
                    McpToolCallLog.workspace_id == request.workspace_id,
                    McpToolCallLog.mcp_server_id == server_id,
                    McpToolCallLog.tool_name == request.tool_name,
                    McpToolCallLog.status.in_(MCP_LIMIT_COUNTED_STATUSES),
                    McpToolCallLog.created_at >= window_started_at,
                )
            )
            if int(current_hour_count or 0) >= max_calls_per_hour:
                self._block(
                    request,
                    "mcp_tool_hourly_call_limit_exceeded",
                    mcp_server_id=server_id,
                )

    def _approval_requester(self) -> McpToolApprovalRequester:
        return McpToolApprovalRequester(self._session)

    def _block(
        self,
        request: McpExecutionRequest,
        reason: str,
        *,
        mcp_server_id: UUID | None = None,
    ) -> None:
        McpExecutionBlocker(self._session).block(
            request,
            reason,
            mcp_server_id=mcp_server_id,
        )
