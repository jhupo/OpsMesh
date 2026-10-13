from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from opsmesh.runtime.instances.events import new_runtime_event
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.spaces.models import RuntimeSpace, RuntimeSpaceEvent
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.db.read_service import PagedQuerySupport
from opsmesh.shared.pagination import PageParams


class AdminRuntimeService(PagedQuerySupport):
    def list_runtime_spaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if workspace_id is not None:
            statement = statement.where(RuntimeSpace.workspace_id == workspace_id)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def quarantine_runtime_space(
        self,
        runtime_space_id: UUID,
        *,
        reason: str,
    ) -> RuntimeSpace | None:
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is None:
            return None
        runtime_space.status = "quarantined"
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type="runtime_space.quarantined",
                message=reason,
                event_metadata={"source": "platform_admin"},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_runtimes(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        statement = select(WorkspaceRuntime).where(WorkspaceRuntime.status != "deleted")
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(WorkspaceRuntime.runtime_space_id == runtime_space_id)
        if status is not None:
            statement = statement.where(WorkspaceRuntime.status == status)
        return self._page(statement.order_by(WorkspaceRuntime.created_at.desc()), page)

    def force_stop_runtime(
        self,
        runtime_id: UUID,
        *,
        reason: str,
    ) -> WorkspaceRuntime | None:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )
        if runtime is None:
            return None
        now = datetime.now(UTC)
        runtime.status = "disabled"
        stage_execution(
            self._session,
            ExecutionRequest(
                workspace_id=runtime.workspace_id,
                resource_id=runtime.id,
                kind=ExecutionKind.SANDBOX_CLEANUP,
            ),
        )
        self._session.add(
            new_runtime_event(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.force_stop_requested",
                message=reason,
                metadata={
                    "source": "platform_admin",
                    "sandbox_cleanup_staged": True,
                },
                created_at=now,
            )
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime
