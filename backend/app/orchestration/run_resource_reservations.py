from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.orchestration.state.resource_usage import (
    merge_usage_max,
    merge_workspace_slot_usage,
    positive_int_usage,
)
from backend.app.orchestration.run_profile_lookup import RunProfileLookup
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceReservation
from backend.app.runtime_spaces.reservation_attachment import (
    RuntimeSpaceReservationAttachmentService,
)
from backend.app.runtime_spaces.reservation_capacity import RuntimeSpaceCapacityReservationService
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.tasks.models import Task, TaskStep
from backend.app.workspaces.models import WorkspaceReservation
from backend.app.workspaces.quotas import WorkspaceQuotaService

MarkStepBlocked = Callable[[TaskStep, str, dict[str, object] | None], None]
MarkStepRunnable = Callable[[TaskStep], None]


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
