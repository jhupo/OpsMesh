import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRunner,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeToolContinuation,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.event_mapping import RuntimeEventTaskMessageMapper
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.agents.models import AgentProfile
from backend.app.audit.service import AuditService
from backend.app.capabilities.adapters import McpAdapterResolver
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    WorkspaceSkillInstall,
)
from backend.app.core.config import Settings
from backend.app.model_providers.resolution import ModelProviderResolutionService
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.orchestration.scheduler import WorkspaceScheduler
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.project_plans import ProjectPlanningService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceReservation
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace
from backend.app.workspaces.quotas import WorkspaceQuotaService

STEP_STATUS_QUEUED = "queued"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_CANCELLED = "cancelled"
TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


@dataclass(frozen=True)
class StaleRunRecoverySummary:
    recovered_runs: int


@dataclass(frozen=True)
class TaskProgressUpdate:
    generic_state: dict[str, object] = field(default_factory=dict)
    domain_state: dict[str, object] = field(default_factory=dict)
    task_input: dict[str, object] = field(default_factory=dict)
    progress: object | None = None
    summary: str | None = None


class RunOrchestrationService:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner or FakeAgentRunner()
        self._settings = settings

    def create_queued_run_for_task(self, task: Task) -> AgentRun | None:
        existing_run = self._existing_active_task_run(task)
        if existing_run is not None:
            return existing_run

        first_team_step = self._create_team_step_plan(task)
        run: AgentRun | None
        if first_team_step is None:
            if task.agent_team_id is not None and task.project_plan is None:
                self._session.flush()
                return None
            generic_run = AgentRun(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                status=RunStatus.QUEUED.value,
                input={"task_id": str(task.id), "title": task.title},
            )
            self._session.add(generic_run)
            run = generic_run
        else:
            run = self._create_reserved_run_for_step(task, first_team_step)

        TaskStateService().transition(task, TaskStatus.QUEUED)
        self._session.flush()
        return run

    def enqueue_run(
        self,
        run: AgentRun,
        requested_by_user_id: UUID | None,
        *,
        force: bool = False,
    ) -> bool:
        if self._queue is None:
            return False

        job = JobPayload(
            workspace_id=run.workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=f"agent.run:{run.workspace_id}:{run.id}",
            priority=self._run_job_priority(run),
            routing=self._run_job_routing(run),
        )
        return self._queue.enqueue(job, force=force)

    def schedule_workspace_steps(
        self,
        *,
        workspace_id: UUID,
        requested_by_user_id: UUID | None = None,
    ) -> list[AgentRun]:
        candidates = [
            step
            for step in self._workspace_eligible_steps(workspace_id)
            if not self._step_has_active_run(step)
        ]
        scheduled_steps = self._scheduler().select_runnable_steps(
            workspace_id=workspace_id,
            candidate_steps=candidates,
        ).runnable_steps
        runs: list[AgentRun] = []
        for step in scheduled_steps:
            task = self._session.get(Task, step.task_id)
            if task is None or task.workspace_id != workspace_id:
                continue
            run = self._create_reserved_run_for_step(task, step)
            if run is None:
                continue
            self.enqueue_run(run, requested_by_user_id)
            runs.append(run)
        self._session.flush()
        return runs

    def schedule_team_steps(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        requested_by_user_id: UUID | None = None,
    ) -> list[AgentRun]:
        candidates = [
            step
            for step in self._team_eligible_steps(workspace_id, team_id)
            if not self._step_has_active_run(step)
        ]
        scheduled_steps = self._scheduler().select_runnable_steps(
            workspace_id=workspace_id,
            candidate_steps=candidates,
        ).runnable_steps
        runs: list[AgentRun] = []
        for step in scheduled_steps:
            task = self._session.get(Task, step.task_id)
            if task is None or task.workspace_id != workspace_id or task.agent_team_id != team_id:
                continue
            run = self._create_reserved_run_for_step(task, step)
            if run is None:
                continue
            self.enqueue_run(run, requested_by_user_id)
            runs.append(run)
        self._session.flush()
        return runs

    def cancel_task(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
    ) -> Task | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        if TaskStatus(task.status) == TaskStatus.CANCELLED:
            raise ValueError("Task is already cancelled")

        completed_at = datetime.now(UTC)
        TaskStateService().transition(task, TaskStatus.CANCELLED, completed_at=completed_at)

        active_runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        ).all()
        for run in active_runs:
            self._mark_run_cancelled(run, completed_at=completed_at)

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.cancelled",
            target_type="task",
            target_id=task.id,
            metadata={"title": task.title, "cancelled_runs": len(active_runs)},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def cancel_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        run = self._session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if run is None:
            return None

        completed_at = datetime.now(UTC)
        self._mark_run_cancelled(run, completed_at=completed_at)
        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                TaskStateService().transition(
                    task,
                    TaskStatus.CANCELLED,
                    completed_at=completed_at,
                )

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.cancelled",
            target_type="agent_run",
            target_id=run.id,
            metadata={"task_id": str(run.task_id) if run.task_id is not None else None},
        )
        self._session.commit()
        self._session.refresh(run)
        return run

    def retry_failed_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        failed_run = self._session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if failed_run is None:
            return None
        if RunStatus(failed_run.status) != RunStatus.FAILED:
            raise ValueError("Only failed runs can be retried")

        task = (
            self._session.get(Task, failed_run.task_id)
            if failed_run.task_id is not None
            else None
        )
        if task is not None:
            TaskStateService().transition(task, TaskStatus.QUEUED)

        retry_run = AgentRun(
            workspace_id=failed_run.workspace_id,
            task_id=failed_run.task_id,
            task_step_id=failed_run.task_step_id,
            agent_profile_id=failed_run.agent_profile_id,
            runtime_id=failed_run.runtime_id,
            runtime_space_id=failed_run.runtime_space_id,
            status=RunStatus.QUEUED.value,
            input=failed_run.input,
            model=failed_run.model,
        )
        self._session.add(retry_run)
        self._session.flush()
        self._append_event(
            retry_run,
            "run.retry_queued",
            f"Retry queued from failed run {failed_run.id}",
        )
        self.enqueue_run(retry_run, actor_user_id)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.retried",
            target_type="agent_run",
            target_id=retry_run.id,
            metadata={
                "failed_run_id": str(failed_run.id),
                "task_id": str(failed_run.task_id) if failed_run.task_id is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(retry_run)
        return retry_run

    def recover_stale_running_runs(
        self,
        *,
        stale_after_seconds: int,
        limit: int = 100,
    ) -> StaleRunRecoverySummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.status == RunStatus.RUNNING.value,
                AgentRun.started_at.is_not(None),
                AgentRun.started_at < cutoff,
            )
            .order_by(AgentRun.started_at.asc())
            .limit(limit)
        ).all()
        for run in stale_runs:
            self._mark_run_recovered_failed(run)
        self._session.commit()
        return StaleRunRecoverySummary(recovered_runs=len(stale_runs))

    def requeue_stale_run(
        self,
        run: AgentRun,
        *,
        requested_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> bool:
        if RunStatus(run.status) != RunStatus.QUEUED:
            raise ValueError("Only queued runs can be requeued")
        run.error = None
        enqueued = self.enqueue_run(run, requested_by_user_id)
        self._append_event(
            run,
            "run.requeued",
            "Requeued by stale run recovery control",
            {
                "reason": reason,
                "enqueued": enqueued,
            },
        )
        return enqueued

    def fail_recovered_run(
        self,
        run: AgentRun,
        *,
        code: str = "stale_worker_run",
        message: str = "Worker stopped reporting before the run completed",
        retryable: bool = True,
        event_message: str = "Marked failed after worker lease expired",
    ) -> None:
        self._mark_run_recovered_failed(
            run,
            code=code,
            message=message,
            retryable=retryable,
            event_message=event_message,
        )

    async def run_agent(self, job: JobPayload) -> AgentRun:
        run = self._session.get(AgentRun, job.resource_id)
        if run is None:
            raise ValueError("Agent run not found")
        if run.workspace_id != job.workspace_id:
            raise ValueError("Agent run workspace mismatch")

        with self._lock_for_run(run) as acquired:
            if not acquired:
                raise RuntimeError("Agent run is already locked")

            if self._run_cancelled_before_execution(run):
                self._session.commit()
                self._session.refresh(run)
                return run

            self._mark_run_started(run)
            used_provider_credentials: set[UUID] = set()
            model_provider_override: dict[str, Any] | None = None
            fallback_selected = False
            while True:
                request = self._build_agent_request(
                    run,
                    job,
                    model_provider_override=model_provider_override,
                )
                if request.model_provider_credential_id is not None:
                    used_provider_credentials.add(request.model_provider_credential_id)
                try:
                    result = await self._agent_runner.run(request)
                    break
                except Exception as exc:
                    self._record_model_provider_failure(
                        run,
                        request.model_provider_credential_id,
                        exc,
                    )
                    fallback = self._next_model_provider_fallback(
                        run=run,
                        failed_request=request,
                        exc=exc,
                        used_provider_credentials=used_provider_credentials,
                    )
                    if fallback is None:
                        self._append_model_provider_fallback_unavailable_event(run, request, exc)
                        self._audit_model_provider_fallback_unavailable(run, request, job, exc)
                        self._mark_run_failed(run, exc)
                        self._session.commit()
                        raise
                    model_provider_override = fallback
                    fallback_selected = True

            if self._run_cancelled_after_model_result(run):
                self._session.commit()
                self._session.refresh(run)
                return run

            if fallback_selected and result.raw_output is None:
                result = AgentRunResult(
                    final_output=result.final_output,
                    raw_output={"model": request.model},
                    events=result.events,
                )
            self._record_model_provider_success(run, request.model_provider_credential_id)
            self._append_model_provider_used_event(run, request)
            self._audit_model_provider_used(run, request, job, fallback_selected=fallback_selected)
            self._map_runtime_events_to_task_messages(run, result)
            if self._agent_result_waiting_runtime(result) or self._run_has_waiting_runtime_event(
                run
            ):
                self._mark_run_waiting_runtime(run)
                self._session.commit()
                self._session.refresh(run)
                return run
            self._mark_run_completed(run, result, job.requested_by_user_id)
            self._session.commit()
            self._session.refresh(run)
            return run

    def _run_cancelled_before_execution(self, run: AgentRun) -> bool:
        self._session.refresh(run)
        status = RunStatus(run.status)
        if status == RunStatus.CANCELLED:
            return True
        if status in TERMINAL_RUN_STATUSES:
            return False
        if not self._linked_task_cancelled(run):
            return False
        self._mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._append_event(
            run,
            "run.skipped_cancelled",
            "Run was skipped because the linked task was already cancelled",
        )
        return True

    def _run_cancelled_after_model_result(self, run: AgentRun) -> bool:
        self._session.refresh(run)
        status = RunStatus(run.status)
        if status == RunStatus.CANCELLED:
            self._append_event(
                run,
                "run.result_discarded_after_cancel",
                "Model result was discarded because the run was cancelled",
            )
            return True
        if status in TERMINAL_RUN_STATUSES:
            return False
        if not self._linked_task_cancelled(run):
            return False
        self._mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._append_event(
            run,
            "run.result_discarded_after_cancel",
            "Model result was discarded because the linked task was cancelled",
        )
        return True

    def _linked_task_cancelled(self, run: AgentRun) -> bool:
        if run.task_id is None:
            return False
        task = self._session.get(Task, run.task_id)
        return task is not None and TaskStatus(task.status) == TaskStatus.CANCELLED

    def run_fake_agent(self, job: JobPayload) -> AgentRun:
        import asyncio

        return asyncio.run(self.run_agent(job))

    def _mark_run_started(self, run: AgentRun) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.RUNNING)
        run.status = RunStatus.RUNNING.value
        run.started_at = datetime.now(UTC)
        self._append_event(run, "run.started", "Fake run started")

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(task, TaskStatus.RUNNING)
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_RUNNING
                self._append_event(run, "task_step.started", step.title)
                self._append_task_message(
                    task_id=step.task_id,
                    workspace_id=step.workspace_id,
                    message_type="step.started",
                    body=step.title,
                    task_step_id=step.id,
                    agent_run_id=run.id,
                    agent_profile_id=run.agent_profile_id,
                    payload=self._step_message_payload(step),
                )

    def _mark_run_waiting_runtime(self, run: AgentRun) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.WAITING_RUNTIME)
        run.status = RunStatus.WAITING_RUNTIME.value
        self._append_event(run, "run.waiting_runtime", "Run is waiting for runtime tool result")

    def _mark_run_completed(
        self,
        run: AgentRun,
        result: AgentRunResult | str,
        requested_by_user_id: UUID | None,
    ) -> None:
        result = _coerce_agent_run_result(result)
        require_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
        final_output = result.final_output
        run.status = RunStatus.COMPLETED.value
        run.output = _run_output_payload(result)
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.completed", "Fake run completed")
        self._release_runtime_space_reservations(run, released_at=run.completed_at)

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                self._apply_task_progress_from_agent_output(
                    task,
                    run=run,
                    final_output=final_output,
                )
                if run.task_step_id is not None:
                    self._mark_step_completed(run, final_output)
                    self._session.flush()
                    next_runs = self._create_and_enqueue_next_step_runs(
                        task,
                        requested_by_user_id=requested_by_user_id,
                    )
                    if not next_runs:
                        next_runs = self.schedule_workspace_steps(
                            workspace_id=run.workspace_id,
                            requested_by_user_id=requested_by_user_id,
                        )
                    if next_runs:
                        return
                    if self._task_has_open_team_work(task):
                        return

                pm_acceptance = self._pm_acceptance_for_completed_run(run, final_output)
                task_output = self._final_output_for_task(
                    task,
                    fallback=run.output,
                    pm_acceptance=pm_acceptance,
                )
                if pm_acceptance is not None and pm_acceptance["decision"] != "approved":
                    self._append_pm_decision_message(
                        task,
                        run=run,
                        pm_acceptance=pm_acceptance,
                    )
                    follow_up_runs = self._materialize_pm_follow_up_work(
                        task,
                        pm_acceptance=pm_acceptance,
                        requested_by_user_id=requested_by_user_id,
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
                    self._append_pm_decision_message(
                        task,
                        run=run,
                        pm_acceptance=pm_acceptance,
                    )

    def _mark_run_failed(self, run: AgentRun, exc: Exception) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        error = normalize_agent_error(exc)
        run.status = RunStatus.FAILED.value
        run.error = error.as_dict()
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.failed", error.message)
        self._release_runtime_space_reservations(run, released_at=run.completed_at)

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=run.completed_at,
                )
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

    def _mark_run_recovered_failed(
        self,
        run: AgentRun,
        *,
        code: str = "stale_worker_run",
        message: str = "Worker stopped reporting before the run completed",
        retryable: bool = True,
        event_message: str = "Marked failed after worker lease expired",
    ) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        run.status = RunStatus.FAILED.value
        run.error = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        run.completed_at = datetime.now(UTC)
        self._append_event(
            run,
            "run.recovered_failed",
            event_message,
        )
        self._release_runtime_space_reservations(run, released_at=run.completed_at)

        if run.task_id is None:
            return
        task = self._session.get(Task, run.task_id)
        if task is None or TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            return
        TaskStateService().transition(task, TaskStatus.FAILED, completed_at=run.completed_at)
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

    def _mark_run_cancelled(self, run: AgentRun, *, completed_at: datetime) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.CANCELLED)
        run.status = RunStatus.CANCELLED.value
        run.error = {
            "code": "cancelled_by_user",
            "message": "Run was cancelled by a workspace user",
            "retryable": False,
        }
        run.completed_at = completed_at
        self._append_event(run, "run.cancelled", "Run was cancelled by a workspace user")
        self._release_runtime_space_reservations(run, released_at=completed_at)
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_CANCELLED

    def _agent_result_waiting_runtime(self, result: AgentRunResult) -> bool:
        for event in result.events:
            if event.event_type == "tool.waiting":
                return True
            if event.payload.get("status") == "waiting_self_hosted":
                return True
        return False

    def _run_has_waiting_runtime_event(self, run: AgentRun) -> bool:
        return self._session.scalar(
            select(RunEvent.id).where(
                RunEvent.workspace_id == run.workspace_id,
                RunEvent.agent_run_id == run.id,
                RunEvent.event_type == "tool.waiting",
            )
        ) is not None

    def _append_model_provider_used_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
    ) -> None:
        self._append_event(
            run,
            "model_provider.used",
            "Model provider handled the run",
            {
                "model_provider": {
                    "model": request.model,
                    "credential_id": str(request.model_provider_credential_id)
                    if request.model_provider_credential_id is not None
                    else None,
                }
            },
        )

    def _audit_model_provider_used(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        *,
        fallback_selected: bool,
    ) -> None:
        if job.requested_by_user_id is None:
            return
        AuditService(self._session).record_user_action(
            workspace_id=run.workspace_id,
            user_id=job.requested_by_user_id,
            action="model_provider.used",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "task_id": str(run.task_id) if run.task_id is not None else None,
                "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run.agent_profile_id is not None
                else None,
                "model": request.model,
                "credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "fallback_selected": fallback_selected,
            },
        )

    def _append_model_provider_fallback_unavailable_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        exc: Exception,
    ) -> None:
        error = normalize_agent_error(exc)
        self._append_event(
            run,
            "model_provider.fallback_unavailable",
            "Model provider fallback was unavailable",
            {
                "reason": error.as_dict(),
                "failed_provider": {
                    "model": request.model,
                    "credential_id": str(request.model_provider_credential_id)
                    if request.model_provider_credential_id is not None
                    else None,
                },
            },
        )

    def _audit_model_provider_fallback_unavailable(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        exc: Exception,
    ) -> None:
        if job.requested_by_user_id is None:
            return
        error = normalize_agent_error(exc)
        AuditService(self._session).record_user_action(
            workspace_id=run.workspace_id,
            user_id=job.requested_by_user_id,
            action="model_provider.fallback_unavailable",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "task_id": str(run.task_id) if run.task_id is not None else None,
                "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run.agent_profile_id is not None
                else None,
                "reason": error.as_dict(),
                "failed_provider": {
                    "model": request.model,
                    "credential_id": str(request.model_provider_credential_id)
                    if request.model_provider_credential_id is not None
                    else None,
                },
            },
        )

    def _append_event(
        self,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RunEvent:
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.agent_run_id == run.id,
                    RunEvent.workspace_id == run.workspace_id,
                )
            )
            or 0
        ) + 1
        event = RunEvent(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            event_type=event_type,
            sequence=next_sequence,
            message=message,
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        self._session.flush([event])
        return event

    def _append_task_message(
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
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                )
            )
            or 0
        ) + 1
        message = TaskMessage(
            workspace_id=workspace_id,
            task_id=task_id,
            task_step_id=task_step_id,
            agent_run_id=agent_run_id,
            agent_profile_id=agent_profile_id,
            message_type=message_type,
            sequence=next_sequence,
            body=body,
            payload=payload or {},
        )
        self._session.add(message)
        self._session.flush([message])
        return message

    def _map_runtime_events_to_task_messages(
        self,
        run: AgentRun,
        result: AgentRunResult,
    ) -> None:
        if run.task_id is None or not result.events:
            return
        step = (
            self._session.get(TaskStep, run.task_step_id)
            if run.task_step_id is not None
            else None
        )
        if step is not None and step.workspace_id != run.workspace_id:
            step = None
        mapper = RuntimeEventTaskMessageMapper()
        for event in result.events:
            self._append_event(
                run,
                event.event_type,
                event.message,
                {"runtime_event": event.payload},
            )
            draft = mapper.map_event(event=event, run=run, step=step)
            if draft is None:
                continue
            self._append_task_message(
                task_id=run.task_id,
                workspace_id=run.workspace_id,
                message_type=draft.message_type,
                body=draft.body,
                task_step_id=run.task_step_id,
                agent_run_id=run.id,
                agent_profile_id=run.agent_profile_id,
                payload=draft.payload,
            )

    def _build_agent_request(
        self,
        run: AgentRun,
        job: JobPayload,
        model_provider_override: dict[str, Any] | None = None,
    ) -> AgentRunRequest:
        self._validate_job_scope(run, job)
        task = self._authorized_task_for_run(run)
        profile = self._authorized_profile_for_run(run)
        if profile is None:
            profile = AgentProfile(
                workspace_id=run.workspace_id,
                name="Default Agent",
                role="worker",
                instructions="Complete the assigned task.",
                model=run.model or "gpt-4.1",
            )

        authorization_snapshot = self._authorization_snapshot_for_run(run)
        self._validate_authorization_snapshot(run, task, profile, authorization_snapshot)
        allowed_tools = self._allowed_tools_for_run(run, profile)
        model_provider = self._model_provider_for_run(
            run,
            profile,
            override=model_provider_override,
        )
        step_context = self._step_context_for_run(run)
        metadata: dict[str, object] = {
            "agent_profile_id": str(profile.id) if profile.id is not None else None,
            "agent_role": profile.role,
            "run_model": model_provider["model"],
            "model_provider_credential_id": str(model_provider["model_provider_credential_id"])
            if model_provider["model_provider_credential_id"] is not None
            else None,
            "authorization_scope": "workspace",
            "authorized_workspace_id": str(run.workspace_id),
            "authorized_task_id": str(task.id) if task is not None else None,
            "tool_policy_source": "agent_profile",
            "authorization_snapshot_version": authorization_snapshot.get("version"),
        }
        metadata.update(step_context)
        continuations = _tool_continuations_for_run(run.input)
        if continuations:
            metadata["tool_continuations"] = [
                {
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "metadata": item.metadata,
                }
                for item in continuations
            ]
        return AgentRunRequest(
            agent_profile=profile,
            input_text=self._input_text_for_run(run),
            context=AgentRuntimeContext(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                run_id=run.id,
                user_id=job.requested_by_user_id,
                allowed_tools=allowed_tools,
                metadata=metadata,
            ),
            model=model_provider["model"],
            base_url=model_provider["base_url"],
            api_key=model_provider["api_key"],
            model_provider_credential_id=model_provider["model_provider_credential_id"],
            tool_executor=BackendToolExecutor.for_mcp_adapter(
                self._session,
                McpAdapterResolver(secret_service=self._mcp_secret_service()),
            )
            if allowed_tools
            else None,
            continuations=continuations,
        )

    def _validate_job_scope(self, run: AgentRun, job: JobPayload) -> None:
        if job.job_type != JobType.AGENT_RUN:
            raise ValueError("Worker job type does not match agent run execution")
        if job.workspace_id != run.workspace_id:
            raise ValueError("Worker job workspace mismatch")
        if job.resource_id != run.id:
            raise ValueError("Worker job resource does not match agent run")

    def _mcp_secret_service(self) -> SecretEncryptionService | None:
        if self._settings is None:
            return None
        return SecretEncryptionService(
            secret=self._settings.credential_encryption_secret,
            key_id=self._settings.credential_encryption_key_id,
        )

    def _authorized_task_for_run(self, run: AgentRun) -> Task | None:
        if run.task_id is None:
            return None
        task = self._session.get(Task, run.task_id)
        if task is None:
            raise ValueError("Run task not found")
        if task.workspace_id != run.workspace_id:
            raise ValueError("Run task workspace mismatch")
        return task

    def _authorized_profile_for_run(self, run: AgentRun) -> AgentProfile | None:
        if run.agent_profile_id is None:
            return None
        profile = self._session.get(AgentProfile, run.agent_profile_id)
        if profile is None:
            raise ValueError("Run agent profile not found")
        if profile.workspace_id != run.workspace_id:
            raise ValueError("Run agent profile workspace mismatch")
        return profile

    def _model_provider_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if override is not None:
            return override
        if self._settings is None:
            return {
                "model": profile.model,
                "base_url": None,
                "api_key": None,
                "model_provider_credential_id": None,
            }
        snapshot = self._authorization_snapshot_for_run(run).get("model_provider")
        credential_id = profile.model_provider_credential_id
        agent_model = profile.model
        if isinstance(snapshot, dict):
            credential_id = _uuid_or_none(snapshot.get("credential_id"))
            selected_model = snapshot.get("selected_model")
            if isinstance(selected_model, str) and selected_model:
                agent_model = selected_model
        return self._resolve_model_provider(
            workspace_id=run.workspace_id,
            credential_id=credential_id,
            agent_model=agent_model,
        )

    def _resolve_model_provider(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        agent_model: str,
    ) -> dict[str, Any]:
        if self._settings is None:
            return {
                "model": agent_model,
                "base_url": None,
                "api_key": None,
                "model_provider_credential_id": None,
            }
        resolved = ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
            ),
        ).resolve_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=credential_id,
            agent_model=agent_model,
        )
        return {
            "model": resolved.model,
            "base_url": resolved.base_url,
            "api_key": resolved.api_key,
            "model_provider_credential_id": resolved.credential_id,
        }

    def _record_model_provider_success(
        self,
        run: AgentRun,
        credential_id: UUID | None,
    ) -> None:
        if self._settings is None:
            return
        ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
            ),
        ).record_success(workspace_id=run.workspace_id, credential_id=credential_id)

    def _record_model_provider_failure(
        self,
        run: AgentRun,
        credential_id: UUID | None,
        exc: Exception,
    ) -> None:
        if self._settings is None:
            return
        error = normalize_agent_error(exc)
        ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
            ),
        ).record_failure(
            workspace_id=run.workspace_id,
            credential_id=credential_id,
            error_code=error.code,
            error_message=error.message,
        )

    def _next_model_provider_fallback(
        self,
        *,
        run: AgentRun,
        failed_request: AgentRunRequest,
        exc: Exception,
        used_provider_credentials: set[UUID],
    ) -> dict[str, Any] | None:
        policy = self._workspace_model_provider_fallback_policy(run.workspace_id)
        if policy is None:
            return None
        error = normalize_agent_error(exc)
        if not error.retryable:
            return None
        retry_error_codes = policy.get("retry_error_codes")
        if isinstance(retry_error_codes, list) and retry_error_codes:
            allowed_codes = {code for code in retry_error_codes if isinstance(code, str)}
            if error.code not in allowed_codes:
                return None
        candidates = policy.get("candidates")
        if not isinstance(candidates, list):
            return None
        for candidate in candidates:
            parsed = _fallback_candidate(candidate)
            if parsed is None:
                continue
            credential_id, model = parsed
            if credential_id in used_provider_credentials:
                continue
            try:
                model_provider = self._resolve_model_provider(
                    workspace_id=run.workspace_id,
                    credential_id=credential_id,
                    agent_model=model or "workspace-default",
                )
            except ValueError:
                continue
            selected_model = model_provider["model"]
            snapshot = ModelProviderResolutionService(self._session).resolve_snapshot_for_agent(
                workspace_id=run.workspace_id,
                agent_credential_id=credential_id,
                agent_model=str(selected_model),
            ).as_dict()
            snapshot["source"] = "fallback_policy"
            self._append_event(
                run,
                "model_provider.fallback_selected",
                "Model provider fallback selected",
                {
                    "reason": error.as_dict(),
                    "failed_provider": {
                        "model": failed_request.model,
                        "credential_id": str(failed_request.model_provider_credential_id)
                        if failed_request.model_provider_credential_id is not None
                        else None,
                    },
                    "model_provider": snapshot,
                },
            )
            return model_provider
        return None

    def _workspace_model_provider_fallback_policy(
        self,
        workspace_id: UUID,
    ) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else None
        if not isinstance(settings, dict):
            return None
        raw_policy = settings.get("model_provider_fallback")
        if raw_policy is None:
            raw_policy = settings.get("model_provider_fallback_policy")
        if not isinstance(raw_policy, dict):
            return None
        if raw_policy.get("enabled") is not True:
            return None
        return raw_policy

    def _input_text_for_run(self, run: AgentRun) -> str:
        task = self._session.get(Task, run.task_id) if run.task_id is not None else None
        if task is None:
            return str(run.input)

        parts = [task.title, task.description]
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                parts.append(f"Current step: {step.title}\n{step.description}".strip())
                if self._is_pm_summary_step(step):
                    parts.append(
                        "PM acceptance output: return JSON with decision "
                        "`approved`, `request_revision`, or `add_missing_work`; include "
                        "`summary`, optional `reasons`, `revision_requests`, and "
                        "`missing_work_packages`."
                    )
                previous_summaries = self._completed_step_summaries(
                    task.id,
                    before=step.order_index,
                )
                if previous_summaries:
                    parts.append("Completed step summaries:\n" + "\n".join(previous_summaries))
        return "\n\n".join(part for part in parts if part).strip()

    def _allowed_tools_for_profile(self, profile: AgentProfile) -> tuple[str, ...]:
        tool_policy = profile.tool_policy if isinstance(profile.tool_policy, dict) else {}
        return _allowed_tools_from_policy(tool_policy)

    def _allowed_tools_for_snapshot(self, snapshot: dict[str, object]) -> tuple[str, ...]:
        tool_policy = snapshot.get("tool_policy")
        return _allowed_tools_from_policy(tool_policy if isinstance(tool_policy, dict) else {})

    def _allowed_tool_policy_for_run(
        self,
        snapshot: dict[str, object],
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        snapshot_policy_tools = self._allowed_tools_for_snapshot(snapshot)
        if snapshot_policy_tools:
            return snapshot_policy_tools
        return self._allowed_tools_for_profile(profile)

    def _allowed_tools_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        snapshot = self._authorization_snapshot_for_run(run)
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list):
            profile_tools = set(self._allowed_tool_policy_for_run(snapshot, profile))
            snapshot_tools = tuple(tool for tool in raw_tools if isinstance(tool, str))
            return tuple(tool for tool in snapshot_tools if tool in profile_tools)
        return self._allowed_tools_for_profile(profile)

    def _authorization_snapshot_for_run(self, run: AgentRun) -> dict[str, object]:
        run_input = run.input if isinstance(run.input, dict) else {}
        snapshot = run_input.get("authorization_snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    def _validate_authorization_snapshot(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        snapshot: dict[str, object],
    ) -> None:
        if not snapshot:
            return
        _expect_optional_uuid(snapshot, "workspace_id", run.workspace_id)
        _expect_optional_uuid(snapshot, "task_id", run.task_id)
        _expect_optional_uuid(snapshot, "task_step_id", run.task_step_id)
        _expect_optional_uuid(snapshot, "agent_profile_id", run.agent_profile_id)
        _expect_optional_uuid(snapshot, "runtime_space_id", run.runtime_space_id)
        if task is not None and task.workspace_id != run.workspace_id:
            raise ValueError("Authorization snapshot task workspace mismatch")
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list):
            profile_tools = set(self._allowed_tool_policy_for_run(snapshot, profile))
            snapshot_tools = {tool for tool in raw_tools if isinstance(tool, str)}
            extra_tools = snapshot_tools - profile_tools
            if extra_tools:
                raise ValueError("Authorization snapshot grants tools outside agent policy")
        installed_skills = snapshot.get("installed_skills")
        if isinstance(installed_skills, list):
            valid_install_snapshots = {
                str(item["install_id"]): item
                for item in self._installed_skill_snapshots(run.workspace_id, profile)
                if isinstance(item.get("install_id"), str)
            }
            for item in installed_skills:
                if not isinstance(item, dict):
                    continue
                install_id = item.get("install_id")
                if not isinstance(install_id, str):
                    continue
                valid_snapshot = valid_install_snapshots.get(install_id)
                if valid_snapshot is None:
                    raise ValueError(
                        "Authorization snapshot references unavailable workspace skill",
                    )
                if not _skill_snapshot_matches(item, valid_snapshot):
                    raise ValueError(
                        "Authorization snapshot workspace skill provenance mismatch",
                    )

    def _step_context_for_run(self, run: AgentRun) -> dict[str, object]:
        if run.task_step_id is None:
            return {}
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            raise ValueError("Run task step workspace mismatch")
        if run.task_id is not None and step.task_id != run.task_id:
            raise ValueError("Run task step does not belong to run task")
        if (
            run.agent_profile_id is not None
            and step.assigned_agent_profile_id is not None
            and step.assigned_agent_profile_id != run.agent_profile_id
        ):
            raise ValueError("Run agent profile is not assigned to task step")
        return {
            "context_scope": "task_step",
            "task_step_id": str(step.id),
            "work_package_id": step.work_package_id,
            "required_role": step.required_role,
            "required_skills": step.required_skills,
            "expected_artifacts": step.expected_artifacts,
            "acceptance_criteria": step.acceptance_criteria,
            "review_policy": step.review_policy,
        }

    def _lock_for_run(self, run: AgentRun) -> AbstractContextManager[bool]:
        if self._queue is not None:
            return self._queue.run_lock(str(run.workspace_id), str(run.id))
        return _NoopLock()

    def _create_team_step_plan(self, task: Task) -> TaskStep | None:
        if task.agent_team_id is None:
            return None
        if self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        ):
            next_steps = self._next_eligible_steps(task.id, task.workspace_id)
            return next_steps[0] if next_steps else None

        snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
        if snapshot is None:
            try:
                snapshot = build_team_snapshot(
                    self._session,
                    workspace_id=task.workspace_id,
                    team_id=task.agent_team_id,
                )
            except ValueError:
                snapshot = None
            else:
                task.team_snapshot = snapshot
                self._session.flush([task])
        if snapshot is not None:
            if task.project_plan is None:
                TaskPlanningAttemptService(
                    self._session,
                    planner=ProjectPlanningService(MemberMatchingService(self._session)),
                ).ensure_initial_plan(task)
                self._session.flush([task])
                if task.project_plan is None:
                    return None
            return self._create_team_step_plan_from_snapshot(task, snapshot)

        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            return None

        members = self._session.scalars(
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == task.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.team_role.asc())
        ).all()
        if team.manager_agent_profile_id is None and not members:
            return None

        first_step: TaskStep | None = None
        manager_step: TaskStep | None = None
        if team.manager_agent_profile_id is not None:
            manager_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=team.manager_agent_profile_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                required_skills=["planning", "coordination"],
                expected_artifacts=["project_plan"],
                acceptance_criteria=["The team has a clear execution plan."],
                review_policy={"reviewer": "manager", "mode": "self_review"},
                title="Manager planning",
                description="Clarify the goal, split responsibilities, and prepare the team plan.",
                status=STEP_STATUS_QUEUED,
                order_index=0,
                dependencies={},
            )
            self._session.add(manager_step)
            self._session.flush([manager_step])
            first_step = manager_step

        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        for index, member in enumerate(members, start=1):
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=member.agent_profile_id,
                work_package_id=f"{member.team_role}-{index}",
                required_role=member.team_role,
                required_skills=[
                    str(skill) for skill in member.skill_weights if isinstance(skill, str)
                ],
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{member.team_role} execution",
                description=f"Complete the assigned team role work for {team.name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self._session.add(step)
            specialist_steps.append(step)
            if first_step is None:
                first_step = step
        self._session.flush(specialist_steps)

        if team.manager_agent_profile_id is not None and specialist_steps:
            summary_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=team.manager_agent_profile_id,
                work_package_id="manager-summary",
                required_role="project_manager",
                required_skills=["review", "synthesis"],
                expected_artifacts=["final_delivery"],
                acceptance_criteria=[
                    "The final answer integrates all completed work packages."
                ],
                review_policy={"reviewer": "user", "mode": "final_acceptance"},
                title="Manager summary",
                description=(
                    "Review specialist outputs, reconcile issues, and produce the final answer."
                ),
                status=STEP_STATUS_QUEUED,
                order_index=1_000,
                dependencies={"after_step_ids": [str(step.id) for step in specialist_steps]},
            )
            self._session.add(summary_step)

        self._session.flush()
        return first_step

    def _create_team_step_plan_from_snapshot(
        self,
        task: Task,
        snapshot: dict[str, object],
    ) -> TaskStep | None:
        project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
        if project_plan is not None:
            planned_step = self._create_team_step_plan_from_project_plan(task, project_plan)
            if planned_step is not None:
                return planned_step

        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None
        raw_members = snapshot.get("members", [])
        members = (
            [member for member in raw_members if isinstance(member, dict)]
            if isinstance(raw_members, list)
            else []
        )
        manager_agent_profile_id = _uuid_or_none(team.get("manager_agent_profile_id"))
        if manager_agent_profile_id is None and not members:
            return None

        team_name = str(team.get("name") or "team")
        first_step: TaskStep | None = None
        manager_step: TaskStep | None = None
        if manager_agent_profile_id is not None:
            manager_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=manager_agent_profile_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                required_skills=["planning", "coordination"],
                expected_artifacts=["project_plan"],
                acceptance_criteria=["The team has a clear execution plan."],
                review_policy={"reviewer": "manager", "mode": "self_review"},
                title="Manager planning",
                description="Clarify the goal, split responsibilities, and prepare the team plan.",
                status=STEP_STATUS_QUEUED,
                order_index=0,
                dependencies={},
            )
            self._session.add(manager_step)
            self._session.flush([manager_step])
            first_step = manager_step

        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        ordered_members = sorted(
            members,
            key=lambda member: (
                _int_or_default(member.get("order_index"), 0),
                str(member.get("team_role") or ""),
            ),
        )
        for index, member in enumerate(ordered_members, start=1):
            agent_profile_id = _uuid_or_none(member.get("agent_profile_id"))
            if agent_profile_id is None:
                continue
            team_role = str(member.get("team_role") or "specialist")
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=agent_profile_id,
                work_package_id=f"{team_role}-{index}",
                required_role=team_role,
                required_skills=_string_list_from_mapping_keys(member.get("skill_weights")),
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{team_role} execution",
                description=f"Complete the assigned team role work for {team_name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self._session.add(step)
            specialist_steps.append(step)
            if first_step is None:
                first_step = step
        self._session.flush(specialist_steps)

        if manager_agent_profile_id is not None and specialist_steps:
            summary_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=manager_agent_profile_id,
                work_package_id="manager-summary",
                required_role="project_manager",
                required_skills=["review", "synthesis"],
                expected_artifacts=["final_delivery"],
                acceptance_criteria=[
                    "The final answer integrates all completed work packages."
                ],
                review_policy={"reviewer": "user", "mode": "final_acceptance"},
                title="Manager summary",
                description=(
                    "Review specialist outputs, reconcile issues, and produce the final answer."
                ),
                status=STEP_STATUS_QUEUED,
                order_index=1_000,
                dependencies={"after_step_ids": [str(step.id) for step in specialist_steps]},
            )
            self._session.add(summary_step)

        self._session.flush()
        return first_step

    def _create_team_step_plan_from_project_plan(
        self,
        task: Task,
        project_plan: dict[str, object],
    ) -> TaskStep | None:
        raw_packages = project_plan.get("work_packages", [])
        if not isinstance(raw_packages, list):
            return None

        created_steps_by_package_id: dict[str, TaskStep] = {}
        first_step: TaskStep | None = None
        for index, package in enumerate(raw_packages):
            if not isinstance(package, dict):
                continue
            package_id = str(package.get("package_id") or f"package-{index}")
            raw_dependencies = package.get("depends_on", [])
            dependencies = (
                [
                    str(dependency)
                    for dependency in raw_dependencies
                    if isinstance(dependency, str)
                ]
                if isinstance(raw_dependencies, list)
                else []
            )
            after_step_ids = [
                str(created_steps_by_package_id[dependency].id)
                for dependency in dependencies
                if dependency in created_steps_by_package_id
            ]
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=_uuid_or_none(package.get("assigned_agent_profile_id")),
                work_package_id=package_id,
                required_role=_optional_string(package.get("required_role")),
                required_skills=_string_list(package.get("required_skills")),
                expected_artifacts=_string_list(package.get("expected_artifacts")),
                acceptance_criteria=_string_list(package.get("acceptance_criteria")),
                review_policy=_dict_or_empty(package.get("review_policy")),
                title=str(package.get("title") or "Work package"),
                description=str(package.get("description") or ""),
                status=STEP_STATUS_QUEUED,
                order_index=index * 100,
                dependencies={
                    "after_step_ids": after_step_ids,
                    "work_package_id": package_id,
                    "required_role": package.get("required_role"),
                    "required_skills": package.get("required_skills", []),
                    "expected_artifacts": package.get("expected_artifacts", []),
                    "acceptance_criteria": package.get("acceptance_criteria", []),
                    "review_policy": package.get("review_policy", {}),
                },
            )
            self._session.add(step)
            self._session.flush([step])
            created_steps_by_package_id[package_id] = step
            if first_step is None and not after_step_ids:
                first_step = step

        self._session.flush()
        return first_step

    def _existing_active_task_run(self, task: Task) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(AgentRun.created_at.asc())
        )

    def _create_run_for_step(self, task: Task, step: TaskStep) -> AgentRun:
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        authorization_snapshot = self._build_authorization_snapshot(
            task,
            step,
            profile,
        )
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=step.assigned_agent_profile_id,
            runtime_space_id=step.runtime_space_id or task.runtime_space_id,
            status=RunStatus.QUEUED.value,
            input={
                "task_id": str(task.id),
                "task_step_id": str(step.id),
                "title": task.title,
                "step_title": step.title,
                "team_orchestration": True,
                "authorization_snapshot": authorization_snapshot,
            },
            model=_run_model_from_snapshot(authorization_snapshot, profile),
        )
        self._session.add(run)
        self._session.flush([run])
        model_provider = authorization_snapshot.get("model_provider")
        if isinstance(model_provider, dict):
            self._append_event(
                run,
                "model_provider.resolved",
                "Model provider resolved for queued run",
                {"model_provider": model_provider},
            )
        return run

    def _create_reserved_run_for_step(self, task: Task, step: TaskStep) -> AgentRun | None:
        locked_step = self._lock_step_for_scheduling(task, step)
        if locked_step is None:
            return None
        step = locked_step
        workspace_usage = self._workspace_resource_usage(task.workspace_id, step)
        workspace_reservation_result = WorkspaceQuotaService(self._session).reserve(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            reservation_key=f"task_step:{step.id}:workspace_run",
            resource_usage=workspace_usage,
        )
        if workspace_reservation_result.reservation is None:
            self._mark_step_scheduling_blocked(
                step,
                workspace_reservation_result.blocked_reason or "workspace_quota_exceeded",
            )
            return None

        reservation_available, reservation = self._reserve_runtime_space_for_step(task, step)
        if not reservation_available:
            WorkspaceQuotaService(self._session).release_reservation(
                workspace_reservation_result.reservation,
                released_at=datetime.now(UTC),
            )
            return None
        run = self._create_run_for_step(task, step)
        WorkspaceQuotaService(self._session).attach_reservation_to_run(
            workspace_reservation_result.reservation,
            run.id,
        )
        if reservation is not None:
            RuntimeSpaceService(self._session).attach_reservation_to_run(reservation, run.id)
        return run

    def _lock_step_for_scheduling(self, task: Task, step: TaskStep) -> TaskStep | None:
        locked_step = self._session.scalar(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.id == step.id,
                TaskStep.task_id == task.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_step is None:
            return None
        if locked_step.status != STEP_STATUS_QUEUED:
            return None
        if self._step_has_active_run(locked_step):
            return None
        return locked_step

    def _reserve_runtime_space_for_step(
        self,
        task: Task,
        step: TaskStep,
    ) -> tuple[bool, RuntimeSpaceReservation | None]:
        runtime_space_id = step.runtime_space_id or task.runtime_space_id
        if runtime_space_id is None:
            self._mark_step_scheduling_runnable(step)
            return True, None
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is not None and runtime_space.status == "paused":
            self._mark_step_scheduling_blocked(step, "runtime_space_paused")
            return False, None
        result = RuntimeSpaceService(self._session).reserve_run_capacity(
            workspace_id=task.workspace_id,
            runtime_space_id=runtime_space_id,
            task_id=task.id,
            task_step_id=step.id,
            reservation_key=f"task_step:{step.id}:run",
            resource_usage=self._runtime_space_resource_usage(
                task.workspace_id,
                runtime_space_id,
                step,
            ),
        )
        if result.reservation is None:
            self._mark_step_scheduling_blocked(
                step,
                result.blocked_reason or "runtime_space_unavailable",
            )
            return False, None
        self._mark_step_scheduling_runnable(step)
        return True, result.reservation

    def _runtime_space_resource_usage(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        step: TaskStep,
    ) -> dict[str, int]:
        usage: dict[str, int] = {"active_runs": 1}
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is not None and runtime_space.workspace_id == workspace_id:
            _merge_usage_max(
                usage,
                _positive_int_dict(runtime_space.policy.get("resource_requirements")),
            )
            _merge_usage_max(
                usage,
                _positive_int_dict(runtime_space.policy.get("reservation_usage")),
            )
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        if profile is not None and profile.workspace_id == workspace_id:
            _merge_usage_max(
                usage,
                _positive_int_dict(profile.runtime_policy.get("resource_requirements")),
            )
            _merge_usage_max(
                usage,
                _positive_int_dict(profile.runtime_policy.get("reservation_usage")),
            )
        _merge_usage_max(
            usage,
            _positive_int_dict(step.dependencies.get("resource_requirements")),
        )
        _merge_usage_max(
            usage,
            _positive_int_dict(step.dependencies.get("reservation_usage")),
        )
        usage["active_runs"] = max(1, usage.get("active_runs", 1))
        return usage

    def _workspace_resource_usage(
        self,
        workspace_id: UUID,
        step: TaskStep,
    ) -> dict[str, int]:
        usage: dict[str, int] = {"active_runs": 1}
        runtime_space_id = step.runtime_space_id
        if runtime_space_id is None:
            task = self._session.get(Task, step.task_id)
            if task is not None and task.workspace_id == workspace_id:
                runtime_space_id = task.runtime_space_id
        if runtime_space_id is not None:
            runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
            if runtime_space is not None and runtime_space.workspace_id == workspace_id:
                _merge_usage_max(
                    usage,
                    _positive_int_dict(runtime_space.policy.get("workspace_reservation_usage")),
                )
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        if profile is not None and profile.workspace_id == workspace_id:
            _merge_usage_max(
                usage,
                _positive_int_dict(profile.runtime_policy.get("workspace_reservation_usage")),
            )
            _merge_workspace_slot_usage(
                usage,
                _positive_int_dict(profile.runtime_policy.get("reservation_usage")),
            )
            _merge_usage_max(
                usage,
                _positive_int_dict(profile.runtime_policy.get("resource_requirements")),
            )
        _merge_usage_max(
            usage,
            _positive_int_dict(step.dependencies.get("workspace_reservation_usage")),
        )
        _merge_workspace_slot_usage(
            usage,
            _positive_int_dict(step.dependencies.get("reservation_usage")),
        )
        _merge_usage_max(
            usage,
            _positive_int_dict(step.dependencies.get("resource_requirements")),
        )
        usage["active_runs"] = max(1, usage.get("active_runs", 1))
        return usage

    def _build_authorization_snapshot(
        self,
        task: Task,
        step: TaskStep,
        profile: AgentProfile | None,
    ) -> dict[str, object]:
        allowed_tools = self._allowed_tools_for_profile(profile) if profile is not None else ()
        tool_policy = profile.tool_policy if profile is not None else {}
        runtime_policy = profile.runtime_policy if profile is not None else {}
        memory_policy = profile.memory_policy if profile is not None else {}
        approval_policy = profile.approval_policy if profile is not None else {}
        installed_skills = self._installed_skill_snapshots(task.workspace_id, profile)
        model_provider = self._model_provider_snapshot(task.workspace_id, profile)
        return {
            "version": 1,
            "workspace_id": str(task.workspace_id),
            "task_id": str(task.id),
            "task_step_id": str(step.id),
            "runtime_space_id": str(step.runtime_space_id or task.runtime_space_id)
            if (step.runtime_space_id or task.runtime_space_id) is not None
            else None,
            "agent_profile_id": str(profile.id)
            if profile is not None and profile.id is not None
            else None,
            "allowed_tools": list(allowed_tools),
            "tool_policy": _dict_copy(tool_policy),
            "installed_skills": installed_skills,
            "model_provider": model_provider,
            "runtime_policy": _runtime_policy_snapshot(runtime_policy),
            "memory_policy": _dict_copy(memory_policy),
            "approval_policy": _dict_copy(approval_policy),
            "file_scope": {
                "mode": "task_step",
                "workspace_id": str(task.workspace_id),
                "task_id": str(task.id),
                "allowed_file_ids": _string_list(step.dependencies.get("allowed_file_ids"))
                if isinstance(step.dependencies, dict)
                else [],
            },
            "runtime_scope": {
                "mode": "workspace_runtime_policy",
                "workspace_id": str(task.workspace_id),
                "task_id": str(task.id),
                "task_step_id": str(step.id),
            },
        }

    def _model_provider_snapshot(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
    ) -> dict[str, object]:
        agent_model = profile.model if profile is not None else "gpt-4.1"
        credential_id = profile.model_provider_credential_id if profile is not None else None
        return ModelProviderResolutionService(self._session).resolve_snapshot_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=credential_id,
            agent_model=agent_model,
        ).as_dict()

    def _installed_skill_snapshots(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
    ) -> list[dict[str, object]]:
        if profile is None or not isinstance(profile.skills, dict):
            return []
        install_ids = _string_list(profile.skills.get("installed_skill_ids")) or _string_list(
            profile.skills.get("skill_install_ids")
        )
        if not install_ids:
            return []
        valid_install_ids = [
            _uuid
            for install_id in install_ids
            if (_uuid := _uuid_or_none(install_id))
        ]
        if not valid_install_ids:
            return []
        installs = self._session.scalars(
            select(WorkspaceSkillInstall).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.status == "active",
                WorkspaceSkillInstall.id.in_(valid_install_ids),
            )
        ).all()
        by_id = {str(install.id): install for install in installs}
        mcp_tool_snapshots = self._installed_skill_mcp_tool_snapshots(workspace_id)
        snapshots: list[dict[str, object]] = []
        for install_id in install_ids:
            install = by_id.get(install_id)
            if install is None:
                continue
            installed_capability_keys = list(install.installed_capability_keys)
            matched_mcp_tools = [
                tool
                for tool in mcp_tool_snapshots
                if _skill_mcp_tool_matches(tool, installed_capability_keys)
            ]
            snapshots.append(
                {
                    "install_id": str(install.id),
                    "source_skill_id": str(install.skill_id),
                    "installed_key": install.installed_key,
                    "installed_name": install.installed_name,
                    "installed_version": install.installed_version,
                    "installed_capability_keys": installed_capability_keys,
                    "source_checksum": install.source_checksum,
                    "source_visibility": install.source_visibility,
                    "mcp_tools": matched_mcp_tools,
                    "mcp_credential_references": _credential_refs_for_tool_snapshots(
                        matched_mcp_tools,
                    ),
                }
            )
        return snapshots

    def _installed_skill_mcp_tool_snapshots(self, workspace_id: UUID) -> list[dict[str, object]]:
        rows = self._session.execute(
            select(McpToolAllowlist, McpServer).join(
                McpServer,
                McpServer.id == McpToolAllowlist.mcp_server_id,
            )
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
            .order_by(McpServer.name.asc(), McpToolAllowlist.tool_name.asc())
        ).all()
        credential_refs = self._mcp_credential_reference_snapshots(workspace_id)
        tools: list[dict[str, object]] = []
        for allow, server in rows:
            refs = [
                ref
                for ref in credential_refs
                if ref.get("mcp_server_id") in {str(server.id), None}
            ]
            tools.append(
                {
                    "allowlist_id": str(allow.id),
                    "mcp_server_id": str(server.id),
                    "mcp_server_name": server.name,
                    "server_type": server.server_type,
                    "tool_name": allow.tool_name,
                    "capability_key": allow.capability_key,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "policy": _dict_copy(allow.policy),
                    "credential_reference_ids": [
                        str(ref["credential_reference_id"]) for ref in refs
                    ],
                    "credential_references": refs,
                }
            )
        return tools

    def _mcp_credential_reference_snapshots(
        self,
        workspace_id: UUID,
    ) -> list[dict[str, object]]:
        refs = self._session.scalars(
            select(McpCredentialReference)
            .where(
                McpCredentialReference.workspace_id == workspace_id,
                McpCredentialReference.status == "active",
            )
            .order_by(McpCredentialReference.created_at.asc())
        ).all()
        return [
            {
                "credential_reference_id": str(ref.id),
                "mcp_server_id": str(ref.mcp_server_id) if ref.mcp_server_id is not None else None,
                "name": ref.name,
                "provider": ref.provider,
                "secret_fingerprint": ref.secret_fingerprint,
                "encryption_key_id": ref.encryption_key_id,
                "scopes": list(ref.scopes),
            }
            for ref in refs
        ]

    def _mark_step_completed(self, run: AgentRun, final_output: str) -> None:
        if run.task_step_id is None:
            return
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return
        step.status = STEP_STATUS_COMPLETED
        step.result_summary = self._step_result_summary(step, final_output)
        self._append_event(run, "task_step.completed", step.title)
        self._append_task_message(
            task_id=step.task_id,
            workspace_id=step.workspace_id,
            message_type="step.completed",
            body=step.result_summary or step.title,
            task_step_id=step.id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                **self._step_message_payload(step),
                "result_summary": step.result_summary,
            },
        )

    def _apply_task_progress_from_agent_output(
        self,
        task: Task,
        *,
        run: AgentRun,
        final_output: str,
    ) -> None:
        progress = _task_progress_from_output(final_output)
        if progress is None:
            return
        changed_fields: list[str] = []
        if progress.generic_state:
            task.generic_state = _deep_merge_dict(task.generic_state, progress.generic_state)
            changed_fields.append("generic_state")
        if progress.domain_state:
            task.domain_state = _deep_merge_dict(task.domain_state, progress.domain_state)
            changed_fields.append("domain_state")
        if progress.task_input:
            task.input = _deep_merge_dict(task.input, progress.task_input)
            changed_fields.append("input")
        if not changed_fields:
            return

        self._append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="task.progress.updated",
            body="Task progress state updated from agent output.",
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                "changed_fields": changed_fields,
                "progress": progress.progress,
                "summary": progress.summary,
                "source": "agent_output",
            },
        )

    def _create_and_enqueue_next_step_runs(
        self,
        task: Task,
        *,
        requested_by_user_id: UUID | None,
    ) -> list[AgentRun]:
        next_runs: list[AgentRun] = []
        eligible_steps = self._next_eligible_steps(task.id, task.workspace_id)
        scheduled_steps = self._scheduler().select_runnable_steps(
            workspace_id=task.workspace_id,
            candidate_steps=eligible_steps,
        ).runnable_steps
        for next_step in scheduled_steps:
            next_run = self._create_reserved_run_for_step(task, next_step)
            if next_run is None:
                continue
            self.enqueue_run(next_run, requested_by_user_id)
            next_runs.append(next_run)
        return next_runs

    def _mark_step_scheduling_runnable(self, step: TaskStep) -> None:
        dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
        dependencies.pop("scheduling_status", None)
        dependencies.pop("blocked_reason", None)
        step.dependencies = dependencies

    def _mark_step_scheduling_blocked(self, step: TaskStep, reason: str) -> None:
        dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
        dependencies["scheduling_status"] = "blocked"
        dependencies["blocked_reason"] = reason
        step.dependencies = dependencies

    def _release_runtime_space_reservations(
        self,
        run: AgentRun,
        *,
        released_at: datetime,
    ) -> None:
        RuntimeSpaceService(self._session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )
        WorkspaceQuotaService(self._session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )

    def _run_job_routing(self, run: AgentRun) -> dict[str, object]:
        routing: dict[str, object] = {}
        priority = self._run_job_priority(run)
        if priority:
            routing["priority"] = priority
        if run.runtime_space_id is None:
            return routing
        routing["runtime_space_id"] = str(run.runtime_space_id)
        runtime_space = self._session.get(RuntimeSpace, run.runtime_space_id)
        if runtime_space is None or runtime_space.workspace_id != run.workspace_id:
            return routing

        runtime_modes = _string_list(runtime_space.policy.get("runtime_modes"))
        runtime_mode = runtime_space.policy.get("runtime_mode")
        if not runtime_modes and isinstance(runtime_mode, str):
            runtime_modes = [runtime_mode]
        capabilities = _string_list(runtime_space.policy.get("worker_capabilities"))
        worker_types = _string_list(runtime_space.policy.get("worker_types"))
        resource_requirements = _positive_number_dict(
            runtime_space.policy.get("resource_requirements"),
        )
        if runtime_modes:
            routing["runtime_modes"] = runtime_modes
        if capabilities:
            routing["capabilities"] = capabilities
        if worker_types:
            routing["worker_types"] = worker_types
        if resource_requirements:
            routing["resource_requirements"] = resource_requirements
        return routing

    def _run_job_priority(self, run: AgentRun) -> int:
        if run.task_id is None:
            return 0
        task = self._session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id:
            return 0
        return int(task.priority or 0)

    def _scheduler(self) -> WorkspaceScheduler:
        return WorkspaceScheduler(self._session)

    def _next_eligible_steps(self, task_id: UUID, workspace_id: UUID) -> list[TaskStep]:
        queued_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_QUEUED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            step
            for step in queued_steps
            if self._dependencies_satisfied(step) and not self._step_has_active_run(step)
        ]

    def _workspace_eligible_steps(self, workspace_id: UUID) -> list[TaskStep]:
        queued_steps = self._session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == STEP_STATUS_QUEUED,
                Task.status.in_(
                    [
                        TaskStatus.QUEUED.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(Task.priority.desc(), TaskStep.order_index.asc())
        ).all()
        return [step for step in queued_steps if self._dependencies_satisfied(step)]

    def _team_eligible_steps(self, workspace_id: UUID, team_id: UUID) -> list[TaskStep]:
        queued_steps = self._session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
                TaskStep.status == STEP_STATUS_QUEUED,
                Task.status.in_(
                    [
                        TaskStatus.QUEUED.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(Task.priority.desc(), TaskStep.order_index.asc())
        ).all()
        return [step for step in queued_steps if self._dependencies_satisfied(step)]

    def _step_has_active_run(self, step: TaskStep) -> bool:
        active_count = self._session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.workspace_id == step.workspace_id,
                AgentRun.task_step_id == step.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        )
        return int(active_count or 0) > 0

    def _task_has_open_team_work(self, task: Task) -> bool:
        incomplete_steps = self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status.in_([STEP_STATUS_QUEUED, STEP_STATUS_RUNNING]),
            )
        )
        if int(incomplete_steps or 0) > 0:
            return True

        active_runs = self._session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        )
        return int(active_runs or 0) > 0

    def _dependencies_satisfied(self, step: TaskStep) -> bool:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        raw_step_ids = dependencies.get("after_step_ids", [])
        if not isinstance(raw_step_ids, list) or not raw_step_ids:
            return True

        dependency_ids: list[UUID] = []
        for raw_step_id in raw_step_ids:
            try:
                dependency_ids.append(UUID(str(raw_step_id)))
            except ValueError:
                return False

        incomplete_count = self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == step.workspace_id,
                TaskStep.id.in_(dependency_ids),
                TaskStep.status != STEP_STATUS_COMPLETED,
            )
        )
        return int(incomplete_count or 0) == 0

    def _completed_step_summaries(self, task_id: UUID, *, before: int) -> list[str]:
        completed_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_COMPLETED,
                TaskStep.order_index < before,
                TaskStep.result_summary.is_not(None),
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            f"- {step.title}: {step.result_summary}"
            for step in completed_steps
            if step.result_summary
        ]

    def _step_result_summary(self, step: TaskStep, final_output: str) -> str:
        if not self._is_pm_summary_step(step):
            return final_output
        pm_acceptance = self._pm_acceptance_from_output(step, final_output)
        summary = pm_acceptance.get("summary")
        return str(summary) if isinstance(summary, str) and summary else final_output

    def _pm_acceptance_for_completed_run(
        self,
        run: AgentRun,
        final_output: str,
    ) -> dict[str, object] | None:
        if run.task_step_id is None:
            return None
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return None
        if not self._is_pm_summary_step(step):
            return None
        return self._pm_acceptance_from_output(step, final_output)

    def _pm_acceptance_from_output(
        self,
        step: TaskStep,
        final_output: str,
    ) -> dict[str, object]:
        raw_output = _json_object_from_text(final_output)
        if raw_output is None:
            return {
                "decision": "approved",
                "summary": final_output,
                "reasons": [],
                "revision_requests": [],
                "missing_work_packages": [],
                "raw_output": final_output,
            }

        decision = _normalize_pm_decision(raw_output.get("decision"))
        if decision is None:
            decision = _normalize_pm_decision(raw_output.get("acceptance_decision"))
        if decision is None:
            decision = _normalize_pm_decision(raw_output.get("status"))
        summary = raw_output.get("summary")
        if not isinstance(summary, str) or not summary:
            summary = raw_output.get("final_output")
        if not isinstance(summary, str) or not summary:
            summary = final_output

        return {
            "decision": decision or "approved",
            "summary": summary,
            "reasons": _string_list_or_single(
                raw_output.get("reasons") or raw_output.get("reason")
            ),
            "revision_requests": _dict_list(raw_output.get("revision_requests")),
            "missing_work_packages": _dict_list(raw_output.get("missing_work_packages")),
            "review_policy": step.review_policy,
            "raw_output": raw_output,
        }

    def _materialize_pm_follow_up_work(
        self,
        task: Task,
        *,
        pm_acceptance: dict[str, object],
        requested_by_user_id: UUID | None,
    ) -> list[AgentRun]:
        summary_step = self._latest_completed_pm_summary_step(task)
        if summary_step is None:
            return []

        revision_cycle = self._next_revision_cycle(task)
        follow_up_steps: list[TaskStep] = []
        follow_up_steps.extend(
            self._create_revision_steps(
                task,
                summary_step=summary_step,
                pm_acceptance=pm_acceptance,
                revision_cycle=revision_cycle,
            )
        )
        follow_up_steps.extend(
            self._create_missing_work_steps(
                task,
                summary_step=summary_step,
                pm_acceptance=pm_acceptance,
                revision_cycle=revision_cycle,
            )
        )
        if not follow_up_steps:
            return []

        self._session.flush(follow_up_steps)
        self._create_follow_up_pm_review_step(
            task,
            summary_step=summary_step,
            follow_up_steps=follow_up_steps,
            pm_acceptance=pm_acceptance,
            revision_cycle=revision_cycle,
        )
        self._append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="pm.follow_up_created",
            body=f"PM created {len(follow_up_steps)} follow-up work package(s).",
            task_step_id=summary_step.id,
            agent_profile_id=summary_step.assigned_agent_profile_id,
            payload={
                "decision": pm_acceptance.get("decision"),
                "revision_cycle": revision_cycle,
                "follow_up_step_ids": [str(step.id) for step in follow_up_steps],
                "follow_up_work_package_ids": [step.work_package_id for step in follow_up_steps],
            },
        )
        return self._create_and_enqueue_next_step_runs(
            task,
            requested_by_user_id=requested_by_user_id,
        )

    def _create_revision_steps(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> list[TaskStep]:
        revision_requests = _dict_list(pm_acceptance.get("revision_requests"))
        steps: list[TaskStep] = []
        for index, request in enumerate(revision_requests, start=1):
            source_step = self._step_for_work_package(
                task,
                _optional_string(request.get("work_package_id")),
            )
            assigned_agent_profile_id = _uuid_or_none(
                request.get("assigned_agent_profile_id")
            ) or (
                source_step.assigned_agent_profile_id if source_step is not None else None
            )
            instruction = _string_or_default(
                request.get("instruction") or request.get("description"),
                "Revise the referenced work package according to the PM review.",
            )
            source_work_package_id = (
                source_step.work_package_id if source_step is not None else "unknown"
            )
            dependency_ids = [str(summary_step.id)]
            if source_step is not None:
                dependency_ids.append(str(source_step.id))
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=assigned_agent_profile_id,
                work_package_id=f"revision-{source_work_package_id}-{revision_cycle}-{index}",
                required_role=_optional_string(request.get("required_role"))
                or (source_step.required_role if source_step is not None else "specialist"),
                required_skills=_string_list(request.get("required_skills"))
                or (source_step.required_skills if source_step is not None else []),
                expected_artifacts=_string_list(request.get("expected_artifacts"))
                or (source_step.expected_artifacts if source_step is not None else ["revision"]),
                acceptance_criteria=_string_list(request.get("acceptance_criteria"))
                or ["The requested revision is addressed without losing prior work."],
                review_policy={"reviewer": "manager", "mode": "revision_review"},
                title=_string_or_default(
                    request.get("title"),
                    f"Revision for {source_work_package_id}",
                ),
                description=instruction,
                status=STEP_STATUS_QUEUED,
                order_index=self._next_follow_up_order_index(task),
                dependencies={
                    "after_step_ids": dependency_ids,
                    "revision_of_work_package_id": source_work_package_id,
                    "pm_acceptance_decision": pm_acceptance.get("decision"),
                    "revision_request": request,
                },
            )
            self._session.add(step)
            steps.append(step)
        return steps

    def _create_missing_work_steps(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> list[TaskStep]:
        missing_packages = _dict_list(pm_acceptance.get("missing_work_packages"))
        steps: list[TaskStep] = []
        for index, package in enumerate(missing_packages, start=1):
            assigned_agent_profile_id = _uuid_or_none(package.get("assigned_agent_profile_id"))
            package_id = _string_or_default(
                package.get("package_id"),
                f"missing-work-{revision_cycle}-{index}",
            )
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=assigned_agent_profile_id,
                work_package_id=package_id,
                required_role=_optional_string(package.get("required_role")) or "specialist",
                required_skills=_string_list(package.get("required_skills")),
                expected_artifacts=_string_list(package.get("expected_artifacts"))
                or ["work_summary"],
                acceptance_criteria=_string_list(package.get("acceptance_criteria"))
                or ["The missing work is completed and ready for review."],
                review_policy={"reviewer": "manager", "mode": "missing_work_review"},
                title=_string_or_default(package.get("title"), "Missing work package"),
                description=_string_or_default(
                    package.get("description") or package.get("instruction"),
                    "Complete the missing work identified by PM review.",
                ),
                status=STEP_STATUS_QUEUED,
                order_index=self._next_follow_up_order_index(task),
                dependencies={
                    "after_step_ids": [str(summary_step.id)],
                    "pm_acceptance_decision": pm_acceptance.get("decision"),
                    "missing_work_package": package,
                },
            )
            self._session.add(step)
            steps.append(step)
        return steps

    def _create_follow_up_pm_review_step(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        follow_up_steps: list[TaskStep],
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> TaskStep:
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=summary_step.assigned_agent_profile_id,
            work_package_id=f"manager-summary-revision-{revision_cycle}",
            required_role=summary_step.required_role or "project_manager",
            required_skills=summary_step.required_skills or ["review", "synthesis"],
            expected_artifacts=summary_step.expected_artifacts or ["final_delivery"],
            acceptance_criteria=summary_step.acceptance_criteria
            or ["The final answer integrates all completed work packages."],
            review_policy=summary_step.review_policy or {
                "reviewer": "user",
                "mode": "final_acceptance",
            },
            title=f"{summary_step.title} revision review",
            description=(
                "Review the completed revision and missing-work outputs, then return a "
                "final PM acceptance decision."
            ),
            status=STEP_STATUS_QUEUED,
            order_index=self._next_follow_up_order_index(task),
            dependencies={
                "after_step_ids": [str(step.id) for step in follow_up_steps],
                "previous_pm_summary_step_id": str(summary_step.id),
                "pm_acceptance_decision": pm_acceptance.get("decision"),
            },
        )
        self._session.add(step)
        self._session.flush([step])
        return step

    def _latest_completed_pm_summary_step(self, task: Task) -> TaskStep | None:
        steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == STEP_STATUS_COMPLETED,
            )
            .order_by(TaskStep.order_index.desc())
        ).all()
        return next((step for step in steps if self._is_pm_summary_step(step)), None)

    def _step_for_work_package(self, task: Task, work_package_id: str | None) -> TaskStep | None:
        if work_package_id is None:
            return None
        return self._session.scalar(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id == work_package_id,
            )
            .order_by(TaskStep.order_index.desc())
        )

    def _next_follow_up_order_index(self, task: Task) -> int:
        max_order = self._session.scalar(
            select(func.max(TaskStep.order_index)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        )
        return int(max_order or 0) + 100

    def _next_revision_cycle(self, task: Task) -> int:
        revision_count = self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id.like("manager-summary-revision-%"),
            )
        )
        return int(revision_count or 0) + 1

    def _is_pm_summary_step(self, step: TaskStep) -> bool:
        review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
        return step.work_package_id == "manager-summary" or review_policy.get(
            "mode"
        ) == "final_acceptance"

    def _append_pm_decision_message(
        self,
        task: Task,
        *,
        run: AgentRun,
        pm_acceptance: dict[str, object],
    ) -> None:
        if run.task_step_id is None:
            return
        decision = str(pm_acceptance.get("decision") or "approved")
        summary = _string_or_default(pm_acceptance.get("summary"), decision)
        self._append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="pm.acceptance_decision",
            body=summary,
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                "decision": decision,
                "reasons": _string_list(pm_acceptance.get("reasons")),
                "revision_requests": _dict_list(pm_acceptance.get("revision_requests")),
                "missing_work_packages": _dict_list(pm_acceptance.get("missing_work_packages")),
            },
        )

    def _step_message_payload(self, step: TaskStep) -> dict[str, object]:
        return {
            "work_package_id": step.work_package_id,
            "required_role": step.required_role,
            "required_skills": step.required_skills,
            "expected_artifacts": step.expected_artifacts,
            "acceptance_criteria": step.acceptance_criteria,
            "review_policy": step.review_policy,
        }

    def _final_output_for_task(
        self,
        task: Task,
        *,
        fallback: dict[str, object] | None,
        pm_acceptance: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        completed_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == STEP_STATUS_COMPLETED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        if not completed_steps:
            return fallback

        final_summary = next(
            (
                step.result_summary
                for step in reversed(completed_steps)
                if step.result_summary is not None
            ),
            None,
        )
        if pm_acceptance is not None and isinstance(pm_acceptance.get("summary"), str):
            final_summary = str(pm_acceptance["summary"])
        return {
            "final_output": final_summary,
            "team_orchestration": {
                "steps": [
                    {
                        "task_step_id": str(step.id),
                        "title": step.title,
                        "status": step.status,
                        "work_package_id": step.work_package_id,
                        "required_role": step.required_role,
                        "agent_profile_id": str(step.assigned_agent_profile_id)
                        if step.assigned_agent_profile_id is not None
                        else None,
                        "result_summary": step.result_summary,
                    }
                    for step in completed_steps
                ],
            },
            "pm_acceptance": pm_acceptance,
        }


class _NoopLock:
    def __enter__(self) -> bool:
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def build_default_queue(redis_client: Any, settings: Any) -> RedisQueue:
    return RedisQueue(
        redis=redis_client,
        keys=RedisKeyBuilder(settings.redis_key_prefix),
        queue_name=settings.worker_queue_name,
    )


def _uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _expect_optional_uuid(
    snapshot: dict[str, object],
    key: str,
    expected: UUID | None,
) -> None:
    raw_value = snapshot.get(key)
    if raw_value is None:
        return
    parsed = _uuid_or_none(raw_value)
    if parsed != expected:
        raise ValueError(f"Authorization snapshot {key} mismatch")


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _allowed_tools_from_policy(tool_policy: dict[str, object]) -> tuple[str, ...]:
    raw_tools = tool_policy.get("allowed_tools")
    if raw_tools is None:
        raw_tools = tool_policy.get("mcp_tools")
    if not isinstance(raw_tools, list):
        return ()
    return tuple(tool for tool in raw_tools if isinstance(tool, str))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list_from_mapping_keys(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [key for key in value if isinstance(key, str)]


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dict_copy(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _runtime_policy_snapshot(value: object) -> dict[str, object]:
    policy = _dict_copy(value)
    raw_mcp = policy.get("mcp")
    mcp_policy = _dict_copy(raw_mcp)
    network_mode = mcp_policy.get("network_mode")
    if not isinstance(network_mode, str) or not network_mode:
        network = policy.get("network")
        network_mode = network if isinstance(network, str) and network else "restricted"
    policy["mcp"] = {
        "network_mode": network_mode,
        "timeout_seconds": _positive_int_or_default(mcp_policy.get("timeout_seconds"), 30),
        "max_input_bytes": _positive_int_or_default(mcp_policy.get("max_input_bytes"), 64_000),
        "max_output_bytes": _positive_int_or_default(
            mcp_policy.get("max_output_bytes"),
            256_000,
        ),
    }
    return policy


def _positive_int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _task_progress_from_output(final_output: str) -> TaskProgressUpdate | None:
    payload = _json_object_from_text(final_output)
    if payload is None:
        return None
    progress_payload = payload.get("task_progress")
    if isinstance(progress_payload, dict):
        payload = progress_payload

    generic_state = _progress_state_dict(
        payload.get("generic_state") or payload.get("state")
    )
    domain_state = _progress_state_dict(
        payload.get("domain_state") or payload.get("domain_progress")
    )
    task_input = _progress_state_dict(payload.get("input") or payload.get("task_input"))
    progress = payload.get("progress")
    summary = payload.get("summary")
    if not isinstance(summary, str):
        summary = payload.get("progress_summary")
    if not isinstance(summary, str):
        summary = None

    if not generic_state and not domain_state and not task_input:
        return None
    return TaskProgressUpdate(
        generic_state=generic_state,
        domain_state=domain_state,
        task_input=task_input,
        progress=progress if isinstance(progress, str | int | float | bool) else None,
        summary=summary,
    )


def _progress_state_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _json_safe_progress_value(item)
        for key, item in value.items()
        if isinstance(key, str) and item is not None
    }


def _json_safe_progress_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_safe_progress_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_safe_progress_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    return str(value)


def _deep_merge_dict(
    existing: dict[str, object],
    update: dict[str, object],
) -> dict[str, object]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


def _tool_continuations_for_run(
    run_input: dict[str, object],
) -> tuple[AgentRuntimeToolContinuation, ...]:
    results = run_input.get("pending_tool_results")
    if not isinstance(results, list):
        return ()
    continuations: list[AgentRuntimeToolContinuation] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool_name")
        status = item.get("status")
        if not isinstance(tool_name, str) or not isinstance(status, str):
            continue
        metadata = {
            key: value
            for key, value in item.items()
            if key not in {"tool_name", "status", "response", "error", "request"}
        }
        continuations.append(
            AgentRuntimeToolContinuation(
                tool_name=tool_name,
                status=status,
                result=_dict_copy(item.get("response")),
                error=_dict_copy(item.get("error")),
                metadata=_dict_copy(metadata),
            )
        )
    return tuple(continuations)


def _skill_snapshot_matches(
    snapshot_item: dict[str, object],
    current_item: dict[str, object],
) -> bool:
    comparable_keys = (
        "install_id",
        "source_skill_id",
        "installed_key",
        "installed_name",
        "installed_version",
        "source_checksum",
        "source_visibility",
    )
    for key in comparable_keys:
        if snapshot_item.get(key) != current_item.get(key):
            return False
    snapshot_caps = snapshot_item.get("installed_capability_keys")
    current_caps = current_item.get("installed_capability_keys")
    if (isinstance(snapshot_caps, list) or isinstance(current_caps, list)) and (
        snapshot_caps != current_caps
    ):
        return False
    if not _optional_list_matches(
        snapshot_item.get("mcp_tools"),
        current_item.get("mcp_tools"),
    ):
        return False
    return _optional_list_matches(
        snapshot_item.get("mcp_credential_references"),
        current_item.get("mcp_credential_references"),
    )


def _skill_mcp_tool_matches(
    tool_snapshot: dict[str, object],
    installed_capability_keys: list[str],
) -> bool:
    capability_key = tool_snapshot.get("capability_key")
    if isinstance(capability_key, str) and capability_key:
        return capability_key in installed_capability_keys
    policy = tool_snapshot.get("policy")
    if isinstance(policy, dict):
        policy_caps = _string_list(policy.get("capability_keys"))
        if policy_caps:
            return any(capability in installed_capability_keys for capability in policy_caps)
    return False


def _credential_refs_for_tool_snapshots(
    tool_snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    refs_by_id: dict[str, dict[str, object]] = {}
    for tool in tool_snapshots:
        credential_refs = tool.get("credential_references")
        if not isinstance(credential_refs, list):
            continue
        for credential_ref in credential_refs:
            if not isinstance(credential_ref, dict):
                continue
            credential_ref_id = credential_ref.get("credential_reference_id")
            if isinstance(credential_ref_id, str):
                refs_by_id.setdefault(credential_ref_id, credential_ref)
    return list(refs_by_id.values())


def _optional_list_matches(snapshot_value: object, current_value: object) -> bool:
    if isinstance(snapshot_value, list):
        return snapshot_value == current_value
    return not isinstance(current_value, list) or current_value == []


def _run_model_from_snapshot(
    snapshot: dict[str, object],
    profile: AgentProfile | None,
) -> str | None:
    model_provider = snapshot.get("model_provider")
    if isinstance(model_provider, dict):
        selected_model = model_provider.get("selected_model")
        if isinstance(selected_model, str) and selected_model:
            return selected_model
    return profile.model if profile is not None else None


def _fallback_candidate(value: object) -> tuple[UUID, str | None] | None:
    if isinstance(value, str):
        credential_id = _uuid_or_none(value)
        return (credential_id, None) if credential_id is not None else None
    if not isinstance(value, dict):
        return None
    credential_id = _uuid_or_none(
        value.get("credential_id") or value.get("model_provider_credential_id")
    )
    if credential_id is None:
        return None
    model = value.get("model")
    return credential_id, model if isinstance(model, str) and model else None


def _positive_number_dict(value: object) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int | float] = {}
    for key, amount in value.items():
        if not isinstance(key, str) or isinstance(amount, bool):
            continue
        if isinstance(amount, int | float) and amount > 0:
            normalized[key] = amount
            continue
        if isinstance(amount, str):
            try:
                parsed = float(amount)
            except ValueError:
                continue
            if parsed > 0:
                normalized[key] = parsed
    return normalized


def _positive_int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, amount in value.items():
        if not isinstance(key, str) or isinstance(amount, bool):
            continue
        if isinstance(amount, int) and amount > 0:
            normalized[key] = amount
            continue
        if isinstance(amount, float) and amount > 0:
            normalized[key] = int(amount)
            continue
        if isinstance(amount, str):
            try:
                parsed = int(amount)
            except ValueError:
                continue
            if parsed > 0:
                normalized[key] = parsed
    return normalized


def _merge_usage_max(target: dict[str, int], update: dict[str, int]) -> None:
    for key, value in update.items():
        target[key] = max(target.get(key, 0), value)


def _merge_workspace_slot_usage(target: dict[str, int], update: dict[str, int]) -> None:
    for key in ("docker_runtimes", "self_hosted_jobs"):
        value = update.get(key)
        if value is not None:
            target[key] = max(target.get(key, 0), value)


def _json_object_from_text(value: str) -> dict[str, object] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _run_output_payload(result: AgentRunResult) -> dict[str, object]:
    payload: dict[str, object] = {"final_output": result.final_output}
    if result.raw_output is not None:
        payload["raw_output"] = _json_safe_object(result.raw_output)
    return payload


def _coerce_agent_run_result(result: AgentRunResult | str) -> AgentRunResult:
    if isinstance(result, AgentRunResult):
        return result
    raw_output = _json_object_from_text(result)
    return AgentRunResult(final_output=result, raw_output=raw_output)


def _json_safe_object(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe_object(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_object(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return _json_safe_object(dumped)
    return str(value)


def _normalize_pm_decision(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "approve": "approved",
        "approved": "approved",
        "complete": "approved",
        "completed": "approved",
        "pass": "approved",
        "request_revision": "request_revision",
        "needs_revision": "request_revision",
        "revision": "request_revision",
        "revise": "request_revision",
        "add_missing_work": "add_missing_work",
        "missing_work": "add_missing_work",
        "add_work": "add_missing_work",
    }
    return aliases.get(normalized)


def _string_list_or_single(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return _string_list(value)


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
