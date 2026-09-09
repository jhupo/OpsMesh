from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.orchestration.pm_acceptance import PmAcceptanceService
from backend.app.orchestration.pm_final_output import PmFinalOutputService
from backend.app.orchestration.pm_follow_up_work import PmFollowUpWorkService
from backend.app.orchestration.pm_step_payload import step_message_payload
from backend.app.orchestration.run_memory_completion import RunMemoryCompletionService
from backend.app.orchestration.run_result_payloads import run_output_payload
from backend.app.orchestration.run_step_completion import TaskStepCompletionService
from backend.app.orchestration.run_task_progress import RunTaskProgressService
from backend.app.orchestration.run_terminal_state import RunTerminalStateService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]
ReleaseRunReservations = Callable[[AgentRun, datetime], None]
SyncConversation = Callable[[AgentRun], None]
CreateNextRuns = Callable[[Task, UUID | None], list[AgentRun]]
ScheduleWorkspaceSteps = Callable[[UUID, UUID | None], list[AgentRun]]
TaskHasOpenTeamWork = Callable[[Task], bool]
@dataclass(slots=True)
class RunLifecycleCallbacks:
    append_event: AppendEvent
    release_reservations: ReleaseRunReservations
    sync_provider_conversation_id: SyncConversation
    create_next_runs: CreateNextRuns
    schedule_workspace_steps: ScheduleWorkspaceSteps
    task_has_open_team_work: TaskHasOpenTeamWork


@dataclass(slots=True)
class RunLifecycleService:
    session: Session
    callbacks: RunLifecycleCallbacks

    def mark_run_started(self, run: AgentRun) -> None:
        RunStateService().transition(run, RunStatus.RUNNING)
        self.session.flush([run])
        self.callbacks.append_event(run, "run.started", "Run started", None)

        if run.task_id is not None:
            task = self.session.scalar(
                select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
            )
            if task is not None:
                TaskStateService().transition(task, TaskStatus.RUNNING)
        if run.task_step_id is not None:
            step = self.session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.task_id == run.task_id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if step is not None:
                TaskStepStateService().transition(step, TaskStepStatus.RUNNING)
                self.callbacks.append_event(run, "task_step.started", step.title, None)
                self.append_task_message(
                    task_id=step.task_id,
                    workspace_id=step.workspace_id,
                    message_type="step.started",
                    body=step.title,
                    task_step_id=step.id,
                    agent_run_id=run.id,
                    agent_profile_id=run.agent_profile_id,
                    payload=step_message_payload(step),
                )

    def mark_run_waiting_runtime(self, run: AgentRun) -> None:
        RunStateService().transition(run, RunStatus.WAITING_RUNTIME)
        self.callbacks.append_event(
            run,
            "run.waiting.runtime",
            "Run is waiting for runtime tool result",
            None,
        )

    def mark_run_waiting_approval(self, run: AgentRun) -> None:
        RunStateService().transition(run, RunStatus.WAITING_APPROVAL)
        self.callbacks.append_event(
            run,
            "run.waiting.approval",
            "Run is waiting for approval",
            None,
        )
        if run.task_id is not None:
            task = self.session.scalar(
                select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
            )
            if task is not None and TaskStatus(task.status) == TaskStatus.RUNNING:
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)

    def mark_run_completed(
        self,
        run: AgentRun,
        result: AgentRunResult,
        requested_by_user_id: UUID | None,
    ) -> None:
        final_output = result.final_output
        RunStateService().transition(
            run,
            RunStatus.COMPLETED,
            output=run_output_payload(result),
        )
        self.callbacks.sync_provider_conversation_id(run)
        self.callbacks.append_event(run, "run.completed", "Run completed", None)
        completed_at = run.completed_at
        if completed_at is None:
            raise ValueError("Terminal run must have a completion timestamp")
        self.callbacks.release_reservations(run, completed_at)
        self._memory_completion().capture(run, result)
        self._memory_completion().expire_working(run)

        if run.task_id is None:
            return
        task = self.session.scalar(
            select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
        )
        if task is None:
            return

        self._task_progress().apply_from_agent_output(
            task,
            run=run,
            final_output=final_output,
        )
        if run.task_step_id is not None:
            self.mark_step_completed(run, final_output)
            self.session.flush()
            next_runs = self.callbacks.create_next_runs(task, requested_by_user_id)
            if not next_runs:
                next_runs = self.callbacks.schedule_workspace_steps(
                    run.workspace_id,
                    requested_by_user_id,
                )
            if next_runs:
                return
            if self.callbacks.task_has_open_team_work(task):
                return

        pm_acceptance_service = PmAcceptanceService(self.session)
        pm_acceptance = pm_acceptance_service.acceptance_for_completed_run(run, final_output)
        task_output = PmFinalOutputService(self.session).final_output_for_task(
            task,
            fallback=run.output,
            pm_acceptance=pm_acceptance,
        )
        if pm_acceptance is not None and pm_acceptance["decision"] != "approved":
            pm_acceptance_service.append_decision_message(
                task,
                run=run,
                pm_acceptance=pm_acceptance,
                append_task_message=self.append_task_message,
            )
            follow_up_runs = PmFollowUpWorkService(self.session).materialize(
                task,
                pm_acceptance=pm_acceptance,
                requested_by_user_id=requested_by_user_id,
                append_task_message=self.append_task_message,
                create_next_runs=self.callbacks.create_next_runs,
            )
            if follow_up_runs:
                if task_output is not None:
                    task.final_output = task_output
                TaskStateService().transition(task, TaskStatus.RUNNING)
            else:
                TaskStateService().transition(
                    task,
                    TaskStatus.WAITING_APPROVAL,
                    final_output=task_output,
                )
            return

        TaskStateService().transition(
            task,
            TaskStatus.COMPLETED,
            completed_at=run.completed_at,
            final_output=task_output,
        )
        self._memory_completion().capture_task_completed(run, task, result)
        if pm_acceptance is not None:
            pm_acceptance_service.append_decision_message(
                task,
                run=run,
                pm_acceptance=pm_acceptance,
                append_task_message=self.append_task_message,
            )

    def mark_run_failed(self, run: AgentRun, exc: Exception) -> None:
        error = normalize_agent_error(exc)
        RunStateService().transition(run, RunStatus.FAILED, error=error.as_dict())
        self.callbacks.append_event(run, "run.failed", error.message, None)
        completed_at = run.completed_at
        if completed_at is None:
            raise ValueError("Terminal run must have a completion timestamp")
        self.callbacks.release_reservations(run, completed_at)

        if run.task_id is not None:
            task = self.session.scalar(
                select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
            )
            if task is not None:
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=run.completed_at,
                )
        if run.task_step_id is not None:
            step = self.session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.task_id == run.task_id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if step is not None:
                TaskStepStateService().transition(step, TaskStepStatus.FAILED)
        self._memory_completion().capture_failed(run, error.as_dict())
        self._memory_completion().expire_working(run)

    def mark_run_recovered_failed(
        self,
        run: AgentRun,
        *,
        code: str = "stale_worker_run",
        message: str = "Worker stopped reporting before the run completed",
        retryable: bool = True,
        event_message: str = "Marked failed after worker lease expired",
    ) -> None:
        self.terminal_states().mark_run_recovered_failed(
            run,
            code=code,
            message=message,
            retryable=retryable,
            event_message=event_message,
        )

    def mark_run_cancelled(self, run: AgentRun, *, completed_at: datetime) -> int:
        return self.terminal_states().mark_run_cancelled(run, completed_at=completed_at)

    def record_worker_cancel_requested(
        self,
        run: AgentRun,
        *,
        requested_at: datetime,
    ) -> int:
        return self.terminal_states().record_worker_cancel_requested(
            run,
            requested_at=requested_at,
        )

    def terminal_states(self) -> RunTerminalStateService:
        return RunTerminalStateService(
            session=self.session,
            append_event=self.callbacks.append_event,
            release_reservations=self.callbacks.release_reservations,
        )

    def agent_result_waiting_runtime(self, result: AgentRunResult) -> bool:
        for event in result.events:
            if event.event_type == "tool.waiting":
                return True
            if event.payload.get("status") == "waiting_self_hosted":
                return True
        return False

    def mark_step_completed(self, run: AgentRun, final_output: str) -> None:
        self._step_completion().mark_step_completed(run, final_output)

    def append_manager_planning_completed_message(
        self,
        step: TaskStep,
        run: AgentRun,
    ) -> None:
        self._step_completion().append_manager_planning_completed_message(step, run)

    def task_message_exists(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        message_type: str,
    ) -> bool:
        return self._step_completion().task_message_exists(
            workspace_id,
            task_id,
            message_type=message_type,
        )

    def _step_completion(self) -> TaskStepCompletionService:
        return TaskStepCompletionService(self.session, self.callbacks.append_event)

    def _memory_completion(self) -> RunMemoryCompletionService:
        return RunMemoryCompletionService(session=self.session)

    def _task_progress(self) -> RunTaskProgressService:
        return RunTaskProgressService(
            session=self.session,
            append_task_message=self.append_task_message,
        )

    def append_task_message(
        self,
        *,
        task_id: UUID,
        workspace_id: UUID,
        message_type: str,
        body: str,
        task_step_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
        payload: dict[str, object] | None = None,
    ) -> TaskMessage:
        return TaskMessageAppendService(self.session).append(
            workspace_id=workspace_id,
            task_id=task_id,
            task_step_id=task_step_id,
            agent_run_id=agent_run_id,
            agent_profile_id=agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload or {},
        )


class NoopLifecycleContext:
    def __enter__(self) -> bool:
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None
