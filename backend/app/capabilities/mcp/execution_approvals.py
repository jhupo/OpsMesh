from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.approvals.policy import ApprovalPolicyDecision
from backend.app.approvals.service import ApprovalService
from backend.app.approvals.waiting import ApprovalWaitingService
from backend.app.capabilities.mcp.execution_logs import McpToolCallLogService
from backend.app.capabilities.mcp.execution_notifications import McpExecutionNotifier
from backend.app.capabilities.mcp.types import McpExecutionRequest, McpExecutionResult
from backend.app.capabilities.mcp_execution_context import (
    authorization_snapshot,
    snapshot_audit_metadata,
)
from backend.app.capabilities.mcp_payloads import payload_hash
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_payload


@dataclass(slots=True)
class McpToolApprovalRequester:
    session: Session

    def request(
        self,
        request: McpExecutionRequest,
        run: AgentRun,
        allow: McpToolAllowlist,
        server: McpServer,
        *,
        reason: str,
        execution_review: ApprovalPolicyDecision | None = None,
    ) -> McpExecutionResult:
        snapshot = authorization_snapshot(run)
        log = McpToolCallLogService(self.session).record(
            request=request,
            server_id=server.id,
            status="waiting_approval",
            response=None,
            error=None,
            snapshot=snapshot,
            run=run,
            latency_ms=0,
        )
        approval = ApprovalService(self.session).create_approval(
            workspace_id=request.workspace_id,
            task_id=run.task_id,
            agent_run_id=run.id,
            requested_by_agent_profile_id=run.agent_profile_id,
            approval_type="mcp.tool",
            risk_level=(
                execution_review.risk_level
                if execution_review is not None
                else allow.risk_level
            ),
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "arguments_sha256": payload_hash(request.arguments),
                "arguments_preview": redact_sensitive_payload(request.arguments),
                "reason": reason,
                "requires_approval": allow.requires_approval,
                "execution_review": execution_review.approval_payload()
                if execution_review is not None
                else None,
                **snapshot_audit_metadata(snapshot),
            },
        )
        log.approval_id = approval.id
        ApprovalWaitingService(self.session).mark_waiting(
            workspace_id=request.workspace_id, run_id=run.id, task_id=run.task_id,
        )
        self._notify_approval_requested(
            request=request,
            run=run,
            allow=allow,
            server=server,
            reason=reason,
            execution_review=execution_review,
            snapshot=snapshot,
        )
        self.session.flush()
        return McpExecutionResult(
            status="waiting_approval",
            response=None,
            error=None,
            log_id=log.id,
            latency_ms=0,
        )

    def _notify_approval_requested(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        allow: McpToolAllowlist,
        server: McpServer,
        reason: str,
        execution_review: ApprovalPolicyDecision | None,
        snapshot: dict[str, object],
    ) -> None:
        notifier = McpExecutionNotifier(self.session)
        review_risk_level = (
            execution_review.risk_level if execution_review is not None else allow.risk_level
        )
        review_reasons = execution_review.reasons if execution_review is not None else []
        notifier.append_run_event(
            run=run,
            event_type="approval.requested",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "risk_level": allow.risk_level,
                "review_risk_level": review_risk_level,
                "review_reasons": review_reasons,
                "reason": reason,
                "requires_approval": allow.requires_approval,
                **snapshot_audit_metadata(snapshot),
            },
        )
        notifier.append_task_message(
            run=run,
            message_type="approval.requested",
            body=f"MCP tool requires approval: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "risk_level": allow.risk_level,
                "review_risk_level": review_risk_level,
                "review_reasons": review_reasons,
                "reason": reason,
                "requires_approval": allow.requires_approval,
            },
        )
