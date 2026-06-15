from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.sessions import PersistentAgentSessionRef
from backend.app.orchestration.pm_acceptance import PmAcceptanceService
from backend.app.orchestration.pm_final_output import PmFinalOutputService
from backend.app.orchestration.pm_follow_up_work import PmFollowUpWorkService
from backend.app.orchestration.pm_step_payload import step_message_payload
from backend.app.orchestration.run_memory_completion import RunMemoryCompletionService
from backend.app.orchestration.run_result_payloads import (
    coerce_agent_run_result,
    run_output_payload,
)
from backend.app.orchestration.run_task_progress import RunTaskProgressService
from backend.app.orchestration.run_terminal_state import RunTerminalStateService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus

STEP_STATUS_RUNNING = "running"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_FAILED = "failed"

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]
ReleaseRunReservations = Callable[[AgentRun, datetime], None]
SyncConversation = Callable[[AgentRun], None]
CreateNextRuns = Callable[[Task, UUID | None], list[AgentRun]]
ScheduleWorkspaceSteps = Callable[[UUID, UUID | None], list[AgentRun]]
TaskHasOpenTeamWork = Callable[[Task], bool]
PersistentSessionRefForRun = Callable[
    [AgentRun, Task | None, object],
    PersistentAgentSessionRef,
]


@dataclass(slots=True)
class RunLifecycleCallbacks:
    append_event: AppendEvent
    release_reservations: ReleaseRunReservations
    sync_provider_conversation_id: SyncConversation
    create_next_runs: CreateNextRuns
    schedule_workspace_steps: ScheduleWorkspaceSteps
    task_has_open_team_work: TaskHasOpenTeamWork
    persistent_session_ref_for_run: PersistentSessionRefForRun


@dataclass(slots=True)
class RunLifecycleService:
    session: Session
    callbacks: RunLifecycleCallbacks

    def mark_run_started(self, run: AgentRun) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.RUNNING)
        run.status = RunStatus.RUNNING.value
        run.started_at = datetime.now(UTC)
        self.session.flush([run])
        self.callbacks.append_event(run, "run.started", "Run started", None)

        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(task, TaskStatus.RUNNING)
        if run.task_step_id is not None:
            step = self.session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_RUNNING
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
        require_run_transition(RunStatus(run.status), RunStatus.WAITING_RUNTIME)
        run.status = RunStatus.WAITING_RUNTIME.value
        self.callbacks.append_event(
            run,
            "run.waiting.runtime",
            "Run is waiting for runtime tool result",
            None,
        )

    def mark_run_completed(
        self,
        run: AgentRun,
        result: AgentRunResult | str,
        requested_by_user_id: UUID | None,
    ) -> None:
        result = coerce_agent_run_result(result)
        require_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
        final_output = result.final_output
        run.status = RunStatus.COMPLETED.value
        run.output = run_output_payload(result)
        run.completed_at = datetime.now(UTC)
        self.callbacks.sync_provider_conversation_id(run)
        self.callbacks.append_event(run, "run.completed", "Run completed", None)
        self.callbacks.release_reservations(run, run.completed_at)
        self._memory_completion().capture_and_compact(run, result)

        if run.task_id is None:
            return
        task = self.session.get(Task, run.task_id)
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
        if pm_acceptance is not None:
            pm_acceptance_service.append_decision_message(
                task,
                run=run,
                pm_acceptance=pm_acceptance,
                append_task_message=self.append_task_message,
            )

    def mark_run_failed(self, run: AgentRun, exc: Exception) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        error = normalize_agent_error(exc)
        run.status = RunStatus.FAILED.value
        run.error = error.as_dict()
        run.completed_at = datetime.now(UTC)
        self.callbacks.append_event(run, "run.failed", error.message, None)
        self.callbacks.release_reservations(run, run.completed_at)

        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=run.completed_at,
                )
        if run.task_step_id is not None:
            step = self.session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

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
        if run.task_step_id is None:
            return
        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return
        step.status = STEP_STATUS_COMPLETED
        step.result_summary = PmAcceptanceService(self.session).step_result_summary(
            step,
            final_output,
        )
        self.callbacks.append_event(run, "task_step.completed", step.title, None)
        self.append_task_message(
            task_id=step.task_id,
            workspace_id=step.workspace_id,
            message_type="step.completed",
            body=step.result_summary or step.title,
            task_step_id=step.id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                **step_message_payload(step),
                "result_summary": step.result_summary,
            },
        )
        if step.work_package_id == "manager-planning":
            self.append_manager_planning_completed_message(step, run)

    def append_manager_planning_completed_message(
        self,
        step: TaskStep,
        run: AgentRun,
    ) -> None:
        if self.task_message_exists(
            step.workspace_id,
            step.task_id,
            message_type="planning.completed",
        ):
            return
        self.append_task_message(
            task_id=step.task_id,
            workspace_id=step.workspace_id,
            message_type="planning.completed",
            body="Project plan generated.",
            task_step_id=step.id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                **step_message_payload(step),
                "source": "manager_planning_step",
                "result_summary": step.result_summary,
            },
        )

    def task_message_exists(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        message_type: str,
    ) -> bool:
        return (
            self.session.scalar(
                select(TaskMessage.id).where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                    TaskMessage.message_type == message_type,
                )
            )
            is not None
        )

    def _memory_completion(self) -> RunMemoryCompletionService:
        return RunMemoryCompletionService(
            session=self.session,
            persistent_session_ref_for_run=self.callbacks.persistent_session_ref_for_run,
        )

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
