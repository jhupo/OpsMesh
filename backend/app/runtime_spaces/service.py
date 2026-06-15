from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.runtime_spaces import (
    RuntimeSpaceDiagnosticsResponse,
)
from backend.app.db.pagination import page_scalars
from backend.app.orchestration.blocked_reasons import explain_blocked_reason
from backend.app.runtime_spaces.diagnostics import RuntimeSpaceDiagnosticsService
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime_spaces.reservations import (
    RuntimeSpaceForceReleaseResult,
    RuntimeSpaceReservationResult,
    RuntimeSpaceReservationService,
)
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam

TARGET_TYPES_BY_SCOPE = {
    "workspace": "workspace",
    "team": "agent_team",
    "task": "task",
}
T = TypeVar("T")
class RuntimeSpaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_runtime_space(
        self,
        *,
        workspace_id: UUID,
        name: str,
        scope: str,
        target_id: UUID | None,
        created_by_user_id: UUID | None,
        default_runtime_template_id: UUID | None,
        policy: dict[str, object],
        network_policy: dict[str, object],
        storage_policy: dict[str, object],
        cleanup_policy: dict[str, object],
        quota_limits: dict[str, int],
    ) -> RuntimeSpace:
        normalized_target_id = self._normalize_target_id(
            workspace_id=workspace_id,
            scope=scope,
            target_id=target_id,
        )
        if default_runtime_template_id is not None:
            self._require_runtime_template(default_runtime_template_id)

        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            default_runtime_template_id=default_runtime_template_id,
            name=name,
            scope=scope,
            policy=policy,
            network_policy=network_policy,
            storage_policy=storage_policy,
            cleanup_policy=cleanup_policy,
        )
        self._session.add(runtime_space)
        self._session.flush([runtime_space])
        self._bind_target(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            target_id=normalized_target_id,
        )
        self._replace_quotas(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            quota_limits=quota_limits,
        )
        self._append_event(
            runtime_space,
            "runtime_space.created",
            f"Runtime space {runtime_space.name} created",
            {
                "scope": scope,
                "target_id": str(normalized_target_id),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_runtime_spaces(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        scope: str | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace).where(RuntimeSpace.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if scope is not None:
            statement = statement.where(RuntimeSpace.scope == scope)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def get_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        return self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )

    def diagnostics(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpaceDiagnosticsResponse | None:
        return RuntimeSpaceDiagnosticsService(self._session).diagnostics(
            workspace_id,
            runtime_space_id,
        )

    def update_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        name: str | None,
        status: str | None,
        policy: dict[str, object] | None,
        network_policy: dict[str, object] | None,
        storage_policy: dict[str, object] | None,
        cleanup_policy: dict[str, object] | None,
        quota_limits: dict[str, int] | None,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        changed_fields: list[str] = []
        for field_name, value in (
            ("name", name),
            ("status", status),
            ("policy", policy),
            ("network_policy", network_policy),
            ("storage_policy", storage_policy),
            ("cleanup_policy", cleanup_policy),
        ):
            if value is not None:
                setattr(runtime_space, field_name, value)
                changed_fields.append(field_name)
        if quota_limits is not None:
            self._replace_quotas(
                workspace_id=workspace_id,
                runtime_space=runtime_space,
                quota_limits=quota_limits,
            )
            changed_fields.append("quota_limits")
        if changed_fields:
            event_type = (
                "runtime_space.status_updated"
                if status is not None and runtime_space.status != old_status
                else "runtime_space.updated"
            )
            self._append_event(
                runtime_space,
                event_type,
                f"Runtime space {runtime_space.name} updated",
                {
                    "changed_fields": changed_fields,
                    "before_status": old_status,
                    "after_status": runtime_space.status,
                },
            )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def reset_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> tuple[RuntimeSpace, int, int, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        runtime_space.status = "active"
        release_result = self._force_release_active_reservations(
            runtime_space=runtime_space,
            reservation_key=None,
        )
        cleared = self._clear_runtime_space_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            codes={
                "runtime_space_paused",
                "runtime_space_unavailable",
                "runtime_space_quota_exceeded",
                "reservation_conflict",
            },
        )
        affected_runtime_ids = [
            str(runtime_id)
            for runtime_id in self._session.scalars(
                select(WorkspaceRuntime.id).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.runtime_space_id == runtime_space_id,
                )
            ).all()
        ]
        self._append_event(
            runtime_space,
            "runtime_space.reset_requested",
            f"Runtime space {runtime_space.name} reset requested",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "released_reservations": release_result.released_reservations,
                "released_keys": release_result.released_keys,
                "cleared_blocked_steps": cleared,
                "affected_runtime_ids": affected_runtime_ids,
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, release_result.released_reservations, cleared, len(
            affected_runtime_ids
        )

    def pause_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reason: str | None,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        runtime_space.status = "paused"
        self._append_event(
            runtime_space,
            "runtime_space.paused",
            f"Runtime space {runtime_space.name} paused",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "reason": _non_empty_string_or_none(reason) or "operator_paused",
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def resume_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> tuple[RuntimeSpace, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        runtime_space.status = "active"
        cleared = self._clear_runtime_space_pause_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
        )
        self._append_event(
            runtime_space,
            "runtime_space.resumed",
            f"Runtime space {runtime_space.name} resumed",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "cleared_blocked_steps": cleared,
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, cleared

    def force_release_reservations(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str | None,
        reason: str | None,
    ) -> tuple[RuntimeSpace, int, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        normalized_key = _non_empty_string_or_none(reservation_key)
        release_result = self._force_release_active_reservations(
            runtime_space=runtime_space,
            reservation_key=normalized_key,
        )
        if release_result.released_reservations == 0:
            self._append_event(
                runtime_space,
                "runtime_space.reservations_force_release_noop",
                f"No active runtime space reservations found for {runtime_space.name}",
                {
                    "reservation_key": normalized_key,
                    "reason": _non_empty_string_or_none(reason),
                },
            )
            self._session.commit()
            self._session.refresh(runtime_space)
            return runtime_space, 0, 0
        cleared = self._clear_runtime_space_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            codes={"runtime_space_quota_exceeded", "reservation_conflict"},
        )
        self._append_event(
            runtime_space,
            "runtime_space.reservations_force_released",
            f"Force released {release_result.released_reservations} runtime space reservations",
            {
                "reservation_key": normalized_key,
                "released_reservations": release_result.released_reservations,
                "released_keys": release_result.released_keys,
                "cleared_blocked_steps": cleared,
                "reason": _non_empty_string_or_none(reason),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, release_result.released_reservations, cleared

    def list_events(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        page: PageParams,
    ) -> tuple[list[RuntimeSpaceEvent], int] | None:
        if self.get_runtime_space(workspace_id, runtime_space_id) is None:
            return None
        statement = (
            select(RuntimeSpaceEvent)
            .where(
                RuntimeSpaceEvent.workspace_id == workspace_id,
                RuntimeSpaceEvent.runtime_space_id == runtime_space_id,
            )
            .order_by(RuntimeSpaceEvent.created_at.desc(), RuntimeSpaceEvent.id)
        )
        return self._page(statement, page)

    def require_runtime_space(self, workspace_id: UUID, runtime_space_id: UUID) -> RuntimeSpace:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None or runtime_space.status != "active":
            raise ValueError("Runtime space not found")
        return runtime_space

    def require_runtime_space_for_target(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> RuntimeSpace:
        runtime_space = self.require_runtime_space(workspace_id, runtime_space_id)
        if runtime_space.scope == "workspace":
            return runtime_space

        expected_target_type = TARGET_TYPES_BY_SCOPE.get(runtime_space.scope)
        if expected_target_type != target_type:
            raise ValueError("Runtime space is not available for this target")
        binding = self._session.scalar(
            select(RuntimeSpaceBinding.id).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.runtime_space_id == runtime_space_id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if binding is None:
            raise ValueError("Runtime space is not available for this target")
        return runtime_space

    def reserve_run_capacity(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        task_id: UUID | None,
        task_step_id: UUID | None,
        reservation_key: str,
        resource_usage: dict[str, int] | None = None,
    ) -> RuntimeSpaceReservationResult:
        return RuntimeSpaceReservationService(self._session).reserve_run_capacity(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            task_id=task_id,
            task_step_id=task_step_id,
            reservation_key=reservation_key,
            resource_usage=resource_usage,
        )

    def attach_reservation_to_run(
        self,
        reservation: RuntimeSpaceReservation,
        agent_run_id: UUID,
    ) -> None:
        RuntimeSpaceReservationService(self._session).attach_reservation_to_run(
            reservation,
            agent_run_id,
        )

    def active_reservation_usage_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
    ) -> dict[str, int]:
        return RuntimeSpaceReservationService(self._session).active_reservation_usage_for_run(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
        )

    def release_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
        released_at: datetime | None = None,
    ) -> int:
        return RuntimeSpaceReservationService(self._session).release_reservations_for_run(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            released_at=released_at,
        )

    def release_reservation_by_key(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str,
        released_at: datetime | None = None,
    ) -> bool:
        return RuntimeSpaceReservationService(self._session).release_reservation_by_key(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            reservation_key=reservation_key,
            released_at=released_at,
        )
    def _normalize_target_id(
        self,
        *,
        workspace_id: UUID,
        scope: str,
        target_id: UUID | None,
    ) -> UUID:
        if scope == "workspace":
            return workspace_id
        if target_id is None:
            raise ValueError("target_id is required for team and task runtime spaces")
        if scope == "team":
            exists = self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == target_id,
                    AgentTeam.status == "active",
                )
            )
            if exists is None:
                raise ValueError("Team not found")
            return target_id
        if scope == "task":
            exists = self._session.scalar(
                select(Task.id).where(Task.workspace_id == workspace_id, Task.id == target_id)
            )
            if exists is None:
                raise ValueError("Task not found")
            return target_id
        raise ValueError("Invalid runtime space scope")

    def _require_runtime_template(self, template_id: UUID) -> None:
        template = self._session.scalar(
            select(RuntimeTemplate.id).where(
                RuntimeTemplate.id == template_id,
                RuntimeTemplate.status == "active",
            )
        )
        if template is None:
            raise ValueError("Runtime template not found")

    def _bind_target(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        target_id: UUID,
    ) -> None:
        target_type = TARGET_TYPES_BY_SCOPE[runtime_space.scope]
        existing = self._session.scalar(
            select(RuntimeSpaceBinding).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if existing is not None:
            raise ValueError("Target already has an active runtime space")
        self._session.add(
            RuntimeSpaceBinding(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space.id,
                target_type=target_type,
                target_id=target_id,
            )
        )

    def _replace_quotas(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        quota_limits: dict[str, int],
    ) -> None:
        existing = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
            )
        ).all()
        existing_by_key = {quota.quota_key: quota for quota in existing}
        for quota_key, limit_value in quota_limits.items():
            quota = existing_by_key.pop(quota_key, None)
            if quota is None:
                quota = RuntimeSpaceQuota(
                    workspace_id=workspace_id,
                    runtime_space_id=runtime_space.id,
                    quota_key=quota_key,
                    limit_value=limit_value,
                )
                self._session.add(quota)
            else:
                quota.limit_value = limit_value
                quota.status = "active"
        for quota in existing_by_key.values():
            quota.status = "disabled"

    def _clear_runtime_space_pause_blocks(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> int:
        return self._clear_runtime_space_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            codes={"runtime_space_paused"},
        )

    def _clear_runtime_space_blocks(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        codes: set[str],
    ) -> int:
        steps = self._session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
                (
                    (TaskStep.runtime_space_id == runtime_space_id)
                    | (
                        TaskStep.runtime_space_id.is_(None)
                        & (Task.runtime_space_id == runtime_space_id)
                    )
                ),
            )
        ).all()
        cleared = 0
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            if explanation.code not in codes:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("blocked_resource_keys", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            cleared += 1
        return cleared

    def _force_release_active_reservations(
        self,
        *,
        runtime_space: RuntimeSpace,
        reservation_key: str | None,
    ) -> RuntimeSpaceForceReleaseResult:
        return RuntimeSpaceReservationService(self._session).force_release_active_reservations(
            runtime_space=runtime_space,
            reservation_key=reservation_key,
        )
    def _append_event(
        self,
        runtime_space: RuntimeSpace,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type=event_type,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
