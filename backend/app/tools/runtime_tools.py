from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.approvals.service import ApprovalService
from backend.app.runs.models import RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import RuntimeCommand, WorkspaceRuntime
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tools.context import ToolContext


@dataclass(frozen=True)
class RuntimeToolResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    approval_required: bool = False
    reason: str | None = None


class RuntimeToolPolicy:
    risky_tokens = {"rm", "mkfs", "shutdown", "reboot", "dd"}

    def requires_approval(self, command: list[str]) -> bool:
        return any(token in self.risky_tokens for token in command)


class RuntimeToolService:
    def __init__(
        self,
        session: Session,
        runtime_manager: RuntimeManager,
        policy: RuntimeToolPolicy | None = None,
    ) -> None:
        self._session = session
        self._runtime_manager = runtime_manager
        self._policy = policy or RuntimeToolPolicy()

    def execute_shell(
        self,
        context: ToolContext,
        *,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeToolResult:
        context.require_tool("runtime_shell")
        self._append_tool_event(context, "tool.called", "runtime_shell")
        if self._policy.requires_approval(command):
            ApprovalService(self._session).create_approval(
                workspace_id=context.workspace_id,
                task_id=context.task_id,
                agent_run_id=context.agent_run_id,
                requested_by_agent_profile_id=None,
                approval_type="runtime.command",
                risk_level="high",
                payload={"command": command, "runtime_id": str(runtime.id)},
            )
            self._mark_waiting_approval(context)
            self._append_tool_event(context, "approval.requested", "runtime_shell")
            self._session.flush()
            return RuntimeToolResult(
                status="waiting_approval",
                approval_required=True,
                reason="Command requires approval",
            )

        record = self._runtime_manager.execute_command(
            workspace_id=context.workspace_id,
            runtime=runtime,
            command=command,
        )
        self._append_tool_event(context, "tool.completed", "runtime_shell")
        self._session.flush()
        return self._from_record(record)

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
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.workspace_id == context.workspace_id,
                    RunEvent.agent_run_id == context.agent_run_id,
                )
            )
            or 0
        ) + 1
        self._session.add(
            RunEvent(
                workspace_id=context.workspace_id,
                agent_run_id=context.agent_run_id,
                event_type=event_type,
                sequence=next_sequence,
                message=message,
                created_at=datetime.now(UTC),
            )
        )

    def _mark_waiting_approval(self, context: ToolContext) -> None:
        if context.agent_run_id is not None:
            from backend.app.runs.models import AgentRun

            run = self._session.get(AgentRun, context.agent_run_id)
            if run is not None:
                run.status = RunStatus.WAITING_APPROVAL.value
        if context.task_id is not None:
            task = self._session.get(Task, context.task_id)
            if task is not None:
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
