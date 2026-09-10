from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.core.errors import DomainError
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_resource_reservations import RunResourceReservationService
from backend.app.orchestration.run_runtime_authorization import (
    RunRuntimeAuthorizationError,
    runtime_binding_for_snapshot,
)
from backend.app.orchestration.scheduler import WorkspaceScheduler
from backend.app.orchestration.step_dependencies import dependencies_satisfied
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep

STEP_STATUS_QUEUED = "queued"

BuildAuthorizationSnapshot = Callable[
    [Task, TaskStep, AgentProfile | None, dict[str, object] | None],
    dict[str, object],
]
ModelProviderBlockedDetails = Callable[[UUID, TaskStep], dict[str, object]]
MarkStepBlocked = Callable[[TaskStep, str, dict[str, object] | None], None]
MarkStepRunnable = Callable[[TaskStep], None]
StepHasActiveRun = Callable[[TaskStep], bool]
TeamSchedulerPolicy = Callable[[UUID, UUID], dict[str, object] | None]


@dataclass(slots=True)
class RunStepLauncher:
    session: Session
    events: RunEventRecorder
    build_authorization_snapshot: BuildAuthorizationSnapshot
    model_provider_blocked_details: ModelProviderBlockedDetails
    mark_step_scheduling_blocked: MarkStepBlocked
    mark_step_scheduling_runnable: MarkStepRunnable
    step_has_active_run: StepHasActiveRun
    team_scheduler_policy: TeamSchedulerPolicy

    def create_run_for_step(
        self,
        task: Task,
        step: TaskStep,
        *,
        authorization_snapshot: dict[str, object] | None = None,
    ) -> AgentRun:
        profile = (
            self.session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == task.workspace_id,
                    AgentProfile.id == step.assigned_agent_profile_id,
                )
            )
            if step.assigned_agent_profile_id is not None
            else None
        )
        agent_snapshot = team_snapshot_agent_for_profile(
            task.team_snapshot,
            step.assigned_agent_profile_id,
        )
        if authorization_snapshot is None:
            authorization_snapshot = self.build_authorization_snapshot(
                task,
                step,
                profile,
                agent_snapshot,
            )
        runtime_binding = runtime_binding_for_snapshot(
            authorization_snapshot,
            workspace_id=task.workspace_id,
        )
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=step.assigned_agent_profile_id,
            runtime_id=(
                runtime_binding.workspace_runtime_id if runtime_binding is not None else None
            ),
            runtime_space_id=(
                runtime_binding.runtime_space_id if runtime_binding is not None else None
            ),
            status=RunStatus.QUEUED.value,
            input={
                "task_id": str(task.id),
                "task_step_id": str(step.id),
                "title": task.title,
                "step_title": step.title,
                "team_orchestration": True,
                "authorization_snapshot": authorization_snapshot,
            },
            model=run_model_from_snapshot(authorization_snapshot, profile),
        )
        self.session.add(run)
        self.session.flush([run])
        RunProjectSnapshotService(self.session).freeze_for_run(run=run, task=task)
        model_provider = authorization_snapshot.get("model_provider")
        if isinstance(model_provider, dict):
            self.events.append_event(
                run,
                "model_provider.resolved",
                "Model provider resolved for queued run",
                {"model_provider": model_provider},
            )
        return run

    def create_reserved_run_for_step(self, task: Task, step: TaskStep) -> AgentRun | None:
        locked_step = self.lock_step_for_scheduling(task, step)
        if locked_step is None:
            return None
        step = locked_step
        member_capacity_decision = self.scheduler().select_runnable_steps(
            workspace_id=task.workspace_id,
            candidate_steps=[step],
            policy_override=self.team_scheduler_policy(task.workspace_id, task.agent_team_id)
            if task.agent_team_id is not None
            else None,
        )
        if step not in member_capacity_decision.runnable_steps:
            return None

        try:
            authorization_snapshot = self._authorization_snapshot(task, step)
            runtime_binding = runtime_binding_for_snapshot(
                authorization_snapshot,
                workspace_id=task.workspace_id,
            )
        except RunRuntimeAuthorizationError as exc:
            self.mark_step_scheduling_blocked(
                step,
                "runtime_authorization_blocked",
                {
                    "error_type": type(exc).__name__,
                    "code": exc.code,
                    "message": str(exc),
                },
            )
            return None
        except DomainError as exc:
            self.mark_step_scheduling_blocked(
                step,
                "capability_authorization_blocked",
                {"code": exc.code, "message": exc.message},
            )
            return None
        except ValueError as exc:
            self._mark_model_provider_blocked(task, step, exc)
            return None

        reservation_service = self._reservations()
        reservations = reservation_service.reserve_for_step(
            task,
            step,
            runtime_space_id=(
                runtime_binding.runtime_space_id if runtime_binding is not None else None
            ),
        )
        if reservations is None:
            return None
        try:
            run = self.create_run_for_step(
                task,
                step,
                authorization_snapshot=authorization_snapshot,
            )
        except RunRuntimeAuthorizationError as exc:
            reservation_service.release_bundle(reservations, released_at=exc_timestamp())
            self.mark_step_scheduling_blocked(
                step,
                "runtime_authorization_blocked",
                {
                    "error_type": type(exc).__name__,
                    "code": exc.code,
                    "message": str(exc),
                },
            )
            return None
        reservation_service.attach_to_run(reservations, run)
        return run

    def _authorization_snapshot(
        self,
        task: Task,
        step: TaskStep,
    ) -> dict[str, object]:
        profile = (
            self.session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == task.workspace_id,
                    AgentProfile.id == step.assigned_agent_profile_id,
                )
            )
            if step.assigned_agent_profile_id is not None
            else None
        )
        return self.build_authorization_snapshot(
            task,
            step,
            profile,
            team_snapshot_agent_for_profile(
                task.team_snapshot,
                step.assigned_agent_profile_id,
            ),
        )

    def _mark_model_provider_blocked(
        self,
        task: Task,
        step: TaskStep,
        exc: ValueError,
    ) -> None:
        self.mark_step_scheduling_blocked(
            step,
            "model_provider_unavailable",
            {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "model_provider": self.model_provider_blocked_details(
                    task.workspace_id,
                    step,
                ),
            },
        )

    def lock_step_for_scheduling(self, task: Task, step: TaskStep) -> TaskStep | None:
        locked_step = self.session.scalar(
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
        if not dependencies_satisfied(self.session, locked_step):
            return None
        if self.step_has_active_run(locked_step):
            return None
        return locked_step

    def scheduler(self) -> WorkspaceScheduler:
        return WorkspaceScheduler(self.session)

    def _reservations(self) -> RunResourceReservationService:
        return RunResourceReservationService(
            session=self.session,
            mark_step_scheduling_blocked=self.mark_step_scheduling_blocked,
            mark_step_scheduling_runnable=self.mark_step_scheduling_runnable,
        )


def team_snapshot_agent_for_profile(
    team_snapshot: object,
    agent_profile_id: UUID | None,
) -> dict[str, object] | None:
    if agent_profile_id is None or not isinstance(team_snapshot, dict):
        return None
    expected_id = str(agent_profile_id)
    raw_members = team_snapshot.get("members")
    if isinstance(raw_members, list):
        for member in raw_members:
            if not isinstance(member, dict):
                continue
            if str(member.get("agent_profile_id") or "") != expected_id:
                continue
            agent = member.get("agent")
            if isinstance(agent, dict):
                return agent
            break
    raw_agents = team_snapshot.get("agents")
    if isinstance(raw_agents, list):
        for agent in raw_agents:
            if not isinstance(agent, dict):
                continue
            snapshot_id = agent.get("id") or agent.get("agent_profile_id")
            if str(snapshot_id or "") == expected_id:
                return agent
    return None


def run_model_from_snapshot(
    snapshot: dict[str, object],
    profile: AgentProfile | None,
) -> str | None:
    model_provider = snapshot.get("model_provider")
    if isinstance(model_provider, dict):
        selected_model = model_provider.get("selected_model")
        if isinstance(selected_model, str) and selected_model:
            return selected_model
    return profile.model if profile is not None else None


def exc_timestamp() -> datetime:
    return datetime.now(UTC)
