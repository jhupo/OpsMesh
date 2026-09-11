from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.runtime_manager.spaces.blockers import RuntimeSpaceBlockerService
from backend.app.runtime_manager.spaces.events import RuntimeSpaceEventLog
from backend.app.runtime_manager.spaces.models import RuntimeSpace
from backend.app.runtime_manager.spaces.quotas import RuntimeSpaceQuotaService
from backend.app.runtime_manager.spaces.reservation_release import (
    RuntimeSpaceReservationReleaseService,
)
from backend.app.runtime_manager.spaces.targets import RuntimeSpaceTargetService
from backend.app.runtime_manager.spaces.utils import non_empty_string_or_none


class RuntimeSpaceLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._targets = RuntimeSpaceTargetService(session)
        self._quotas = RuntimeSpaceQuotaService(session)
        self._events = RuntimeSpaceEventLog(session)
        self._blockers = RuntimeSpaceBlockerService(session)
        self._reservations = RuntimeSpaceReservationReleaseService(session)

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
        normalized_target_id = self._targets.normalize_target_id(
            workspace_id=workspace_id,
            scope=scope,
            target_id=target_id,
        )
        if default_runtime_template_id is not None:
            self._targets.require_runtime_template(default_runtime_template_id)

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
        self._targets.bind_target(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            target_id=normalized_target_id,
        )
        self._quotas.replace_quotas(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            quota_limits=quota_limits,
        )
        self._events.append(
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

    def update_runtime_space(
        self,
        runtime_space: RuntimeSpace,
        *,
        workspace_id: UUID,
        name: str | None,
        status: str | None,
        policy: dict[str, object] | None,
        network_policy: dict[str, object] | None,
        storage_policy: dict[str, object] | None,
        cleanup_policy: dict[str, object] | None,
        quota_limits: dict[str, int] | None,
    ) -> RuntimeSpace:
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
            self._quotas.replace_quotas(
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
            self._events.append(
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
        runtime_space: RuntimeSpace,
    ) -> tuple[RuntimeSpace, int, int, int]:
        old_status = runtime_space.status
        runtime_space.status = "active"
        release_result = self._reservations.force_release_active_reservations(
            runtime_space=runtime_space,
            reservation_key=None,
        )
        cleared = self._blockers.clear_blocks(
            workspace_id=runtime_space.workspace_id,
            runtime_space_id=runtime_space.id,
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
                    WorkspaceRuntime.workspace_id == runtime_space.workspace_id,
                    WorkspaceRuntime.runtime_space_id == runtime_space.id,
                )
            ).all()
        ]
        self._events.append(
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
        return (
            runtime_space,
            release_result.released_reservations,
            cleared,
            len(affected_runtime_ids),
        )

    def pause_runtime_space(
        self,
        runtime_space: RuntimeSpace,
        *,
        reason: str | None,
    ) -> RuntimeSpace:
        old_status = runtime_space.status
        runtime_space.status = "paused"
        self._events.append(
            runtime_space,
            "runtime_space.paused",
            f"Runtime space {runtime_space.name} paused",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "reason": non_empty_string_or_none(reason) or "operator_paused",
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def resume_runtime_space(self, runtime_space: RuntimeSpace) -> tuple[RuntimeSpace, int]:
        old_status = runtime_space.status
        runtime_space.status = "active"
        cleared = self._blockers.clear_pause_blocks(
            workspace_id=runtime_space.workspace_id,
            runtime_space_id=runtime_space.id,
        )
        self._events.append(
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
        runtime_space: RuntimeSpace,
        *,
        reservation_key: str | None,
        reason: str | None,
    ) -> tuple[RuntimeSpace, int, int]:
        normalized_key = non_empty_string_or_none(reservation_key)
        release_result = self._reservations.force_release_active_reservations(
            runtime_space=runtime_space,
            reservation_key=normalized_key,
        )
        if release_result.released_reservations == 0:
            self._events.append(
                runtime_space,
                "runtime_space.reservations_force_release_noop",
                f"No active runtime space reservations found for {runtime_space.name}",
                {
                    "reservation_key": normalized_key,
                    "reason": non_empty_string_or_none(reason),
                },
            )
            self._session.commit()
            self._session.refresh(runtime_space)
            return runtime_space, 0, 0
        cleared = self._blockers.clear_blocks(
            workspace_id=runtime_space.workspace_id,
            runtime_space_id=runtime_space.id,
            codes={"runtime_space_quota_exceeded", "reservation_conflict"},
        )
        self._events.append(
            runtime_space,
            "runtime_space.reservations_force_released",
            f"Force released {release_result.released_reservations} runtime space reservations",
            {
                "reservation_key": normalized_key,
                "released_reservations": release_result.released_reservations,
                "released_keys": release_result.released_keys,
                "cleared_blocked_steps": cleared,
                "reason": non_empty_string_or_none(reason),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, release_result.released_reservations, cleared
