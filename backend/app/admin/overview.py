from __future__ import annotations

from sqlalchemy import select

from backend.app.admin.base import AdminSessionService
from backend.app.core.pagination import PageParams
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.models import Workspace


class AdminOverviewService(AdminSessionService):
    def overview(self) -> dict[str, int]:
        return {
            "workspaces_total": self._count(select(Workspace)),
            "workspaces_active": self._count(select(Workspace).where(Workspace.status == "active")),
            "workers_total": self._count(select(WorkerNode)),
            "workers_online": self._count(select(WorkerNode).where(WorkerNode.status == "online")),
            "workers_draining": self._count(
                select(WorkerNode).where(WorkerNode.status == "draining")
            ),
            "active_worker_leases": self._count(
                select(WorkerLease).where(WorkerLease.status == "running")
            ),
            "runtime_spaces_total": self._count(select(RuntimeSpace)),
            "runtime_spaces_quarantined": self._count(
                select(RuntimeSpace).where(RuntimeSpace.status == "quarantined")
            ),
            "runtimes_running": self._count(
                select(WorkspaceRuntime).where(WorkspaceRuntime.status == "running")
            ),
            "runtimes_offline": self._count(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.connection_status == "offline",
                    WorkspaceRuntime.status != "deleted",
                )
            ),
            "critical_security_events": self._count(
                select(SecurityEvent).where(SecurityEvent.severity == "critical")
            ),
        }

    def list_workspaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[Workspace], int]:
        statement = select(Workspace)
        if status is not None:
            statement = statement.where(Workspace.status == status)
        return self._page(statement.order_by(Workspace.created_at.desc()), page)
