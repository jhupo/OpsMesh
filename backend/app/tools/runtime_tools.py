from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.approvals.policy import ApprovalPolicyDecision, ApprovalPolicyEngine
from backend.app.approvals.service import ApprovalService
from backend.app.approvals.waiting import ApprovalWaitingService
from backend.app.core.config import Settings
from backend.app.runs.event_writer import RunEventWriter
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import RuntimeCommand, WorkspaceRuntime
from backend.app.tools.context import ToolContext


@dataclass(frozen=True)
class RuntimeToolResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    approval_required: bool = False
    reason: str | None = None


class RuntimeToolService:
    def __init__(
        self,
        session: Session,
        runtime_manager: RuntimeManager,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._runtime_manager = runtime_manager
        self._settings = settings

    def execute_shell(
        self,
        context: ToolContext,
        *,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeToolResult:
        context.require_tool("runtime_shell")
        self._append_tool_event(context, "tool.called", "runtime_shell")
        execution_review = ApprovalPolicyEngine(
            self._session,
            self._settings,
        ).evaluate_runtime_command(
            workspace_id=context.workspace_id,
            command=command,
            context=_runtime_review_context(context, runtime),
        )
        if execution_review.blocked:
            self._append_tool_event(context, "tool.blocked", "runtime_shell")
            self._session.flush()
            return RuntimeToolResult(
                status="blocked",
                reason=_runtime_denial_reason(execution_review),
            )
        if execution_review.required:
            self._request_runtime_command_approval(
                context,
                runtime=runtime,
                command=command,
                execution_review=execution_review,
            )
            return RuntimeToolResult(
                status="waiting_approval",
                approval_required=True,
                reason="Runtime command requires approval",
            )

        record = self._runtime_manager.execute_command(
            workspace_id=context.workspace_id,
            runtime=runtime,
            command=command,
        )
        self._append_tool_event(context, "tool.completed", "runtime_shell")
        self._session.flush()
        return self._from_record(record)

    def _request_runtime_command_approval(
        self,
        context: ToolContext,
        *,
        runtime: WorkspaceRuntime,
        command: list[str],
        execution_review: ApprovalPolicyDecision,
    ) -> None:
        ApprovalService(self._session).create_approval(
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.agent_run_id,
            requested_by_agent_profile_id=None,
            approval_type="runtime.command",
            risk_level=execution_review.risk_level,
            payload={
                "command_preview": command,
                "runtime_id": str(runtime.id),
                "execution_review": execution_review.approval_payload(),
            },
        )
        self._mark_waiting_approval(context)
        self._append_tool_event(context, "approval.requested", "runtime_shell")
        self._session.flush()

    def _from_record(self, record: RuntimeCommand) -> RuntimeToolResult:
        return RuntimeToolResult(
            status=record.status,
            stdout=record.stdout,
            stderr=record.stderr,
            exit_code=record.exit_code,
        )

    def _append_tool_event(self, context: ToolContext, event_type: str, message: str) -> None:
        if context.agent_run_id is None:
            return
        RunEventWriter(self._session).append(
            workspace_id=context.workspace_id, run_id=context.agent_run_id,
            event_type=event_type, message=message,
        )

    def _mark_waiting_approval(self, context: ToolContext) -> None:
        ApprovalWaitingService(self._session).mark_waiting(
            workspace_id=context.workspace_id, run_id=context.agent_run_id, task_id=context.task_id,
        )


def _runtime_review_context(
    context: ToolContext,
    runtime: WorkspaceRuntime,
) -> dict[str, object]:
    return {
        "agent_run_id": str(context.agent_run_id) if context.agent_run_id is not None else None,
        "task_id": str(context.task_id) if context.task_id is not None else None,
        "runtime_id": str(runtime.id),
        "runtime_provider": runtime.runtime_provider,
    }


def _runtime_denial_reason(decision: ApprovalPolicyDecision) -> str:
    if "platform.runtime_command.disabled" in decision.reasons:
        return "Runtime commands are disabled by platform safety policy"
    if "policy.review.unavailable" in decision.reasons:
        return "Runtime command review is unavailable"
    return "High-risk runtime commands are disabled by platform safety policy"
