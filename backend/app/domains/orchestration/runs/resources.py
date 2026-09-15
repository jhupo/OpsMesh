from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.workspace.tenants.models import WorkspaceReservation
from backend.app.domains.workspace.tenants.quotas import WorkspaceQuotaService
from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceReservation
from backend.app.runtime.environment.spaces.reservation_attachment import (
    RuntimeSpaceReservationAttachmentService,
)
from backend.app.runtime.environment.spaces.reservation_capacity import (
    RuntimeSpaceCapacityReservationService,
)
from backend.app.runtime.environment.spaces.reservation_release import (
    RuntimeSpaceReservationReleaseService,
)

MarkStepBlocked = Callable[[TaskStep, str, dict[str, object] | None], None]
MarkStepRunnable = Callable[[TaskStep], None]


def positive_numeric_usage(value: object) -> dict[str, int | float]:
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


def positive_int_usage(value: object) -> dict[str, int]:
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


def scheduler_numeric_limits(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    result = {
        str(key): float(raw_value)
        for key, raw_value in value.items()
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool) and raw_value >= 0
    }
    return result or None


def step_resource_requirements(step: TaskStep) -> dict[str, float]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    raw_requirements = dependencies.get("resource_requirements")
    if not isinstance(raw_requirements, dict):
        return {}
    return {
        str(key): float(raw_value)
        for key, raw_value in raw_requirements.items()
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool) and raw_value > 0
    }


def merge_usage_max(target: dict[str, int], update: dict[str, int]) -> None:
    for key, value in update.items():
        target[key] = max(target.get(key, 0), value)


def merge_workspace_slot_usage(target: dict[str, int], update: dict[str, int]) -> None:
    for key in ("docker_runtimes", "self_hosted_jobs"):
        value = update.get(key)
        if value is not None:
            target[key] = max(target.get(key, 0), value)


class RunProfileLookup:
    def __init__(self, session: Session) -> None:
        self._session = session

    def for_step(self, workspace_id: UUID, step: TaskStep) -> AgentProfile | None:
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        if profile is None or profile.workspace_id != workspace_id:
            return None
        return profile


@dataclass(frozen=True, slots=True)
class RunReservationBundle:
    workspace_reservation: WorkspaceReservation
    runtime_space_reservation: RuntimeSpaceReservation | None


@dataclass(slots=True)
class RunResourceReservationService:
    session: Session
    mark_step_scheduling_blocked: MarkStepBlocked
    mark_step_scheduling_runnable: MarkStepRunnable

    def reserve_for_step(
        self,
        task: Task,
        step: TaskStep,
        *,
        runtime_space_id: UUID | None = None,
    ) -> RunReservationBundle | None:
        workspace_reservation = self._reserve_workspace_quota(task, step)
        if workspace_reservation is None:
            return None

        reservation_available, runtime_space_reservation = self._reserve_runtime_space(
            task,
            step,
            runtime_space_id=runtime_space_id,
        )
        if not reservation_available:
            WorkspaceQuotaService(self.session).release_reservation(
                workspace_reservation,
                released_at=datetime.now(UTC),
            )
            return None

        return RunReservationBundle(
            workspace_reservation=workspace_reservation,
            runtime_space_reservation=runtime_space_reservation,
        )

    def attach_to_run(self, bundle: RunReservationBundle, run: AgentRun) -> None:
        WorkspaceQuotaService(self.session).attach_reservation_to_run(
            bundle.workspace_reservation,
            run.id,
        )
        if bundle.runtime_space_reservation is not None:
            RuntimeSpaceReservationAttachmentService(self.session).attach_reservation_to_run(
                bundle.runtime_space_reservation,
                run.id,
            )

    def release_bundle(self, bundle: RunReservationBundle, *, released_at: datetime) -> None:
        WorkspaceQuotaService(self.session).release_reservation(
            bundle.workspace_reservation,
            released_at=released_at,
        )
        if bundle.runtime_space_reservation is not None:
            RuntimeSpaceReservationReleaseService(self.session).release_reservation_by_key(
                workspace_id=bundle.runtime_space_reservation.workspace_id,
                runtime_space_id=bundle.runtime_space_reservation.runtime_space_id,
                reservation_key=bundle.runtime_space_reservation.reservation_key,
                released_at=released_at,
            )

    def release_for_run(self, run: AgentRun, *, released_at: datetime) -> None:
        RuntimeSpaceReservationReleaseService(self.session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )
        WorkspaceQuotaService(self.session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )

    def _reserve_workspace_quota(
        self,
        task: Task,
        step: TaskStep,
    ) -> WorkspaceReservation | None:
        result = WorkspaceQuotaService(self.session).reserve(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            reservation_key=f"task_step:{step.id}:workspace_run",
            resource_usage=self.workspace_usage(task.workspace_id, step),
        )
        if result.reservation is not None:
            return result.reservation
        self.mark_step_scheduling_blocked(
            step,
            result.blocked_reason or "workspace_quota_exceeded",
            None,
        )
        return None

    def _reserve_runtime_space(
        self,
        task: Task,
        step: TaskStep,
        *,
        runtime_space_id: UUID | None,
    ) -> tuple[bool, RuntimeSpaceReservation | None]:
        runtime_space_id = runtime_space_id or step.runtime_space_id or task.runtime_space_id
        if runtime_space_id is None:
            self.mark_step_scheduling_runnable(step)
            return True, None
        runtime_space = self.session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is not None and runtime_space.status == "paused":
            self.mark_step_scheduling_blocked(step, "runtime_space_paused", None)
            return False, None
        result = RuntimeSpaceCapacityReservationService(self.session).reserve_run_capacity(
            workspace_id=task.workspace_id,
            runtime_space_id=runtime_space_id,
            task_id=task.id,
            task_step_id=step.id,
            reservation_key=f"task_step:{step.id}:run",
            resource_usage=self.runtime_space_usage(
                task.workspace_id,
                runtime_space_id,
                step,
            ),
        )
        if result.reservation is None:
            self.mark_step_scheduling_blocked(
                step,
                result.blocked_reason or "runtime_space_unavailable",
                None,
            )
            return False, None
        self.mark_step_scheduling_runnable(step)
        return True, result.reservation

    def runtime_space_usage(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        step: TaskStep,
    ) -> dict[str, int]:
        usage: dict[str, int] = {"active_runs": 1}
        runtime_space = self.session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is not None and runtime_space.workspace_id == workspace_id:
            merge_usage_max(
                usage,
                positive_int_usage(runtime_space.policy.get("resource_requirements")),
            )
            merge_usage_max(
                usage,
                positive_int_usage(runtime_space.policy.get("reservation_usage")),
            )
        profile = self._profiles().for_step(workspace_id, step)
        if profile is not None:
            merge_usage_max(
                usage,
                positive_int_usage(profile.runtime_policy.get("resource_requirements")),
            )
            merge_usage_max(
                usage,
                positive_int_usage(profile.runtime_policy.get("reservation_usage")),
            )
        merge_usage_max(
            usage,
            positive_int_usage(step.dependencies.get("resource_requirements")),
        )
        merge_usage_max(
            usage,
            positive_int_usage(step.dependencies.get("reservation_usage")),
        )
        usage["active_runs"] = max(1, usage.get("active_runs", 1))
        return usage

    def workspace_usage(self, workspace_id: UUID, step: TaskStep) -> dict[str, int]:
        usage: dict[str, int] = {"active_runs": 1}
        runtime_space_id = step.runtime_space_id
        if runtime_space_id is None:
            task = self.session.get(Task, step.task_id)
            if task is not None and task.workspace_id == workspace_id:
                runtime_space_id = task.runtime_space_id
        if runtime_space_id is not None:
            runtime_space = self.session.get(RuntimeSpace, runtime_space_id)
            if runtime_space is not None and runtime_space.workspace_id == workspace_id:
                merge_usage_max(
                    usage,
                    positive_int_usage(runtime_space.policy.get("workspace_reservation_usage")),
                )
        profile = self._profiles().for_step(workspace_id, step)
        if profile is not None:
            merge_usage_max(
                usage,
                positive_int_usage(profile.runtime_policy.get("workspace_reservation_usage")),
            )
            merge_workspace_slot_usage(
                usage,
                positive_int_usage(profile.runtime_policy.get("reservation_usage")),
            )
            merge_usage_max(
                usage,
                positive_int_usage(profile.runtime_policy.get("resource_requirements")),
            )
        merge_usage_max(
            usage,
            positive_int_usage(step.dependencies.get("workspace_reservation_usage")),
        )
        merge_workspace_slot_usage(
            usage,
            positive_int_usage(step.dependencies.get("reservation_usage")),
        )
        merge_usage_max(
            usage,
            positive_int_usage(step.dependencies.get("resource_requirements")),
        )
        usage["active_runs"] = max(1, usage.get("active_runs", 1))
        return usage

    def _profiles(self) -> RunProfileLookup:
        return RunProfileLookup(self.session)
