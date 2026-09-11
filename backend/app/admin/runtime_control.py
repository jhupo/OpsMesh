from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from backend.app.admin.base import AdminSessionService
from backend.app.core.pagination import PageParams
from backend.app.runtime_manager.models import RuntimeEvent, RuntimeLease, WorkspaceRuntime
from backend.app.runtime_manager.spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class AdminRuntimeService(AdminSessionService):
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

    def list_runtime_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        workspace_runtime_id: UUID | None = None,
    ) -> tuple[list[RuntimeLease], int]:
        statement = select(RuntimeLease)
        if status is not None:
            statement = statement.where(RuntimeLease.status == status)
        if workspace_id is not None:
            statement = statement.where(RuntimeLease.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(RuntimeLease.runtime_space_id == runtime_space_id)
        if workspace_runtime_id is not None:
            statement = statement.where(RuntimeLease.workspace_runtime_id == workspace_runtime_id)
        return self._page(statement.order_by(RuntimeLease.created_at.desc()), page)

    def list_runtimes(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        status: str | None = None,
        connection_status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        statement = select(WorkspaceRuntime).where(WorkspaceRuntime.status != "deleted")
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(WorkspaceRuntime.runtime_space_id == runtime_space_id)
        if status is not None:
            statement = statement.where(WorkspaceRuntime.status == status)
        if connection_status is not None:
            statement = statement.where(WorkspaceRuntime.connection_status == connection_status)
        return self._page(statement.order_by(WorkspaceRuntime.created_at.desc()), page)

    def force_stop_runtime(
        self,
        runtime_id: UUID,
        *,
        reason: str,
        queue: RedisQueue | None = None,
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
        runtime.status = "stopping"
        runtime.connection_status = "degraded"
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        if lease is not None and lease.status in {"running", "acquired"}:
            lease.lease_metadata = {
                **lease.lease_metadata,
                "stop_requested_by": "platform_admin",
                "stop_request_reason": reason,
                "stop_requested_at": now.isoformat(),
            }
        if queue is not None:
            queue.enqueue(
                JobPayload(
                    workspace_id=runtime.workspace_id,
                    job_type=JobType.RUNTIME_CONTROL,
                    resource_id=runtime.id,
                    idempotency_key=f"admin.runtime.stop:{runtime.workspace_id}:{runtime.id}",
                    routing={
                        "action": "stop",
                        "reason": reason,
                        "source": "platform_admin",
                        "force": True,
                    },
                    priority=100,
                    max_attempts=3,
                )
            )
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.force_stop_requested",
                message=reason,
                event_metadata={
                    "source": "platform_admin",
                    "runtime_lease_id": str(lease.id) if lease is not None else None,
                    "runtime_lease_release_pending": lease is not None,
                    "worker_control_enqueued": queue is not None,
                },
                created_at=now,
            )
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime
