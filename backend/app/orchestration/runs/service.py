from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.planning.team_step_planner import TeamStepPlanner
from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.run_eligibility import RunEligibilityService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_request.builder import RunRequestBuilder
from backend.app.orchestration.runs.authorization_snapshot import RunAuthorizationSnapshotService
from backend.app.orchestration.runs.job_routing import RunJobRoutingService
from backend.app.orchestration.runs.lifecycle import RunLifecycleCallbacks, RunLifecycleService
from backend.app.orchestration.runs.resources import RunResourceReservationService
from backend.app.orchestration.runtime.authorization import runtime_binding_for_snapshot
from backend.app.orchestration.scheduler.main import WorkspaceScheduler
from backend.app.orchestration.steps.launcher import RunStepLauncher
from backend.app.orchestration.steps.scheduling_state import (
    mark_step_scheduling_blocked,
    mark_step_scheduling_runnable,
)
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.teams.models import AgentTeam
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue

__all__ = [
    "RunOrchestrationService",
    "build_default_queue",
]


class RunOrchestrationService:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
    ) -> None:
        self._session = session
        self._queue = queue

    def create_queued_run_for_task(self, task: Task) -> AgentRun | None:
        existing_run = self._existing_active_task_run(task)
        if existing_run is not None:
            return existing_run

        if self._requires_initial_project_plan(task):
            if task.team_snapshot is None and task.agent_team_id is not None:
                task.team_snapshot = build_team_snapshot(
                    self._session,
                    workspace_id=task.workspace_id,
                    team_id=task.agent_team_id,
                )
            TaskPlanningAttemptService(self._session).ensure_initial_plan(task)
            if task.project_plan is None:
                self._session.flush()
                return None

        first_team_step = self._team_step_planner().create_team_step_plan(task)
        run: AgentRun | None
        if first_team_step is None:
            if task.agent_team_id is not None:
                return None
            authorization_snapshot = self._authorization_snapshots().build_authorization_snapshot(
                task,
                None,
                None,
            )
            runtime_binding = runtime_binding_for_snapshot(
                authorization_snapshot,
                workspace_id=task.workspace_id,
            )
            generic_run = AgentRun(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_id=(
                    runtime_binding.workspace_runtime_id if runtime_binding is not None else None
                ),
                runtime_space_id=(
                    runtime_binding.runtime_space_id if runtime_binding is not None else None
                ),
                status=RunStatus.QUEUED.value,
                input={
                    "task_id": str(task.id),
                    "title": task.title,
                    "authorization_snapshot": authorization_snapshot,
                },
            )
            self._session.add(generic_run)
            self._session.flush([generic_run])
            RunProjectSnapshotService(self._session).freeze_for_run(
                run=generic_run,
                task=task,
            )
            run = generic_run
        else:
            run = self._run_step_launcher().create_reserved_run_for_step(task, first_team_step)

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
            priority=self._run_job_routing().priority(run),
            routing=self._run_job_routing().routing(run),
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
            for step in self._eligibility().workspace_eligible_steps(workspace_id)
            if not self._eligibility().step_has_active_run(step)
        ]
        scheduled_steps = (
            self._scheduler()
            .select_runnable_steps(
                workspace_id=workspace_id,
                candidate_steps=candidates,
            )
            .runnable_steps
        )
        runs: list[AgentRun] = []
        for step in scheduled_steps:
            task = self._session.scalar(
                select(Task).where(Task.workspace_id == workspace_id, Task.id == step.task_id)
            )
            if task is None:
                continue
            run = self._run_step_launcher().create_reserved_run_for_step(task, step)
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
            for step in self._eligibility().team_eligible_steps(workspace_id, team_id)
            if not self._eligibility().step_has_active_run(step)
        ]
        scheduled_steps = (
            self._scheduler()
            .select_runnable_steps(
                workspace_id=workspace_id,
                candidate_steps=candidates,
                policy_override=self._team_scheduler_policy(workspace_id, team_id),
            )
            .runnable_steps
        )
        runs: list[AgentRun] = []
        for step in scheduled_steps:
            task = self._session.scalar(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.id == step.task_id,
                    Task.agent_team_id == team_id,
                )
            )
            if task is None:
                continue
            run = self._run_step_launcher().create_reserved_run_for_step(task, step)
            if run is None:
                continue
            self.enqueue_run(run, requested_by_user_id)
            runs.append(run)
        self._session.flush()
        return runs

    def _team_scheduler_policy(
        self,
        workspace_id: UUID,
        team_id: UUID,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None or not isinstance(team.default_task_policy, dict):
            return None
        scheduler = team.default_task_policy.get("scheduler")
        return dict(scheduler) if isinstance(scheduler, dict) else None

    def _run_lifecycle(self) -> RunLifecycleService:
        return RunLifecycleService(
            self._session,
            RunLifecycleCallbacks(
                append_event=self._run_events().append_event,
                release_reservations=self._release_runtime_reservations_for_lifecycle,
                sync_provider_conversation_id=self._request_builder().sync_provider_conversation_id,
                create_next_runs=lambda task, user_id: self._create_and_enqueue_next_step_runs(
                    task,
                    requested_by_user_id=user_id,
                ),
                schedule_workspace_steps=self._schedule_workspace_steps_for_lifecycle,
                task_has_open_team_work=self._eligibility().task_has_open_team_work,
            ),
        )

    def _release_runtime_reservations_for_lifecycle(
        self,
        run: AgentRun,
        released_at: datetime,
    ) -> None:
        self._run_reservations().release_for_run(run, released_at=released_at)

    def _schedule_workspace_steps_for_lifecycle(
        self,
        workspace_id: UUID,
        requested_by_user_id: UUID | None,
    ) -> list[AgentRun]:
        return self.schedule_workspace_steps(
            workspace_id=workspace_id,
            requested_by_user_id=requested_by_user_id,
        )

    def _run_events(self) -> RunEventRecorder:
        return RunEventRecorder(self._session)

    def _run_job_routing(self) -> RunJobRoutingService:
        return RunJobRoutingService(self._session)

    def _run_reservations(self) -> RunResourceReservationService:
        return RunResourceReservationService(
            session=self._session,
            mark_step_scheduling_blocked=mark_step_scheduling_blocked,
            mark_step_scheduling_runnable=mark_step_scheduling_runnable,
        )

    def _request_builder(self) -> RunRequestBuilder:
        return RunRequestBuilder(self._session, None)

    def _authorization_snapshots(self) -> RunAuthorizationSnapshotService:
        return RunAuthorizationSnapshotService(self._session, self._request_builder())

    def _eligibility(self) -> RunEligibilityService:
        return RunEligibilityService(self._session)

    def _team_step_planner(self) -> TeamStepPlanner:
        return TeamStepPlanner(self._session)

    def _requires_initial_project_plan(self, task: Task) -> bool:
        if task.agent_team_id is None or task.project_plan is not None:
            return False
        return (
            self._session.scalar(
                select(TaskStep.id)
                .where(
                    TaskStep.workspace_id == task.workspace_id,
                    TaskStep.task_id == task.id,
                )
                .limit(1)
            )
            is None
        )

    def _run_step_launcher(self) -> RunStepLauncher:
        return RunStepLauncher(
            session=self._session,
            events=self._run_events(),
            build_authorization_snapshot=lambda task, step, profile, agent_snapshot: (
                self._authorization_snapshots().build_authorization_snapshot(
                    task,
                    step,
                    profile,
                    agent_snapshot=agent_snapshot,
                )
            ),
            model_provider_blocked_details=(
                self._authorization_snapshots().model_provider_blocked_details
            ),
            mark_step_scheduling_blocked=mark_step_scheduling_blocked,
            mark_step_scheduling_runnable=mark_step_scheduling_runnable,
            step_has_active_run=self._eligibility().step_has_active_run,
            team_scheduler_policy=self._team_scheduler_policy,
        )

    def _existing_active_task_run(self, task: Task) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
            .order_by(AgentRun.created_at.asc())
        )

    def _create_and_enqueue_next_step_runs(
        self,
        task: Task,
        *,
        requested_by_user_id: UUID | None,
    ) -> list[AgentRun]:
        next_runs: list[AgentRun] = []
        eligible_steps = self._eligibility().next_eligible_steps(task.id, task.workspace_id)
        scheduled_steps = (
            self._scheduler()
            .select_runnable_steps(
                workspace_id=task.workspace_id,
                candidate_steps=eligible_steps,
            )
            .runnable_steps
        )
        for next_step in scheduled_steps:
            next_run = self._run_step_launcher().create_reserved_run_for_step(task, next_step)
            if next_run is None:
                continue
            self.enqueue_run(next_run, requested_by_user_id)
            next_runs.append(next_run)
        return next_runs

    def _scheduler(self) -> WorkspaceScheduler:
        return WorkspaceScheduler(self._session)


def build_default_queue(redis_client: Any, settings: Any) -> RedisQueue:
    return RedisQueue(
        redis=redis_client,
        keys=RedisKeyBuilder(settings.redis_key_prefix),
        queue_name=settings.worker_queue_name,
        tracing_enabled=settings.tracing_enabled,
    )
