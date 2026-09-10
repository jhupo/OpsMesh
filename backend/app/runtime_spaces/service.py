from datetime import datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.runtime_spaces import RuntimeSpaceDiagnosticsResponse
from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.runtime_spaces.diagnostics import RuntimeSpaceDiagnosticsService
from backend.app.runtime_spaces.lifecycle import RuntimeSpaceLifecycleService
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceEvent,
    RuntimeSpaceReservation,
)
from backend.app.runtime_spaces.reservation_attachment import (
    RuntimeSpaceReservationAttachmentService,
)
from backend.app.runtime_spaces.reservation_capacity import (
    RuntimeSpaceCapacityReservationService,
    RuntimeSpaceReservationResult,
)
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.runtime_spaces.targets import RuntimeSpaceTargetService

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
        return RuntimeSpaceLifecycleService(self._session).create_runtime_space(
            workspace_id=workspace_id,
            name=name,
            scope=scope,
            target_id=target_id,
            created_by_user_id=created_by_user_id,
            default_runtime_template_id=default_runtime_template_id,
            policy=policy,
            network_policy=network_policy,
            storage_policy=storage_policy,
            cleanup_policy=cleanup_policy,
            quota_limits=quota_limits,
        )

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
        return RuntimeSpaceLifecycleService(self._session).update_runtime_space(
            runtime_space,
            workspace_id=workspace_id,
            name=name,
            status=status,
            policy=policy,
            network_policy=network_policy,
            storage_policy=storage_policy,
            cleanup_policy=cleanup_policy,
            quota_limits=quota_limits,
        )

    def reset_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> tuple[RuntimeSpace, int, int, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        return RuntimeSpaceLifecycleService(self._session).reset_runtime_space(runtime_space)

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
        return RuntimeSpaceLifecycleService(self._session).pause_runtime_space(
            runtime_space,
            reason=reason,
        )

    def resume_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> tuple[RuntimeSpace, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        return RuntimeSpaceLifecycleService(self._session).resume_runtime_space(runtime_space)

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
        return RuntimeSpaceLifecycleService(self._session).force_release_reservations(
            runtime_space,
            reservation_key=reservation_key,
            reason=reason,
        )

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
        return RuntimeSpaceTargetService(self._session).require_runtime_space_for_target(
            runtime_space,
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=target_id,
        )

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
        return RuntimeSpaceCapacityReservationService(self._session).reserve_run_capacity(
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
        RuntimeSpaceReservationAttachmentService(self._session).attach_reservation_to_run(
            reservation,
            agent_run_id,
        )

    def active_reservation_usage_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
    ) -> dict[str, int]:
        return RuntimeSpaceReservationAttachmentService(
            self._session
        ).active_reservation_usage_for_run(
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
        return RuntimeSpaceReservationReleaseService(self._session).release_reservations_for_run(
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
        return RuntimeSpaceReservationReleaseService(self._session).release_reservation_by_key(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            reservation_key=reservation_key,
            released_at=released_at,
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
