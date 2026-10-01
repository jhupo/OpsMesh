from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from backend.app.governance.audit.service import AuditService
from backend.app.shared.db.read_service import AdminSessionService
from backend.app.shared.pagination import PageParams
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember
from backend.app.workspaces.projects.models import WorkspaceProject


class AdminWorkspaceManagementService(AdminSessionService):
    def list_workspaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        owner_user_id: UUID | None = None,
    ) -> tuple[list[Workspace], int]:
        statement = select(Workspace)
        if status is not None:
            statement = statement.where(Workspace.status == status)
        if owner_user_id is not None:
            statement = statement.where(Workspace.owner_user_id == owner_user_id)
        return self._page(statement.order_by(Workspace.created_at.desc()), page)

    def get_workspace(self, workspace_id: UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def workspace_counts(self, workspace_id: UUID) -> tuple[int, int]:
        member_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.status == "active",
                )
            )
            or 0
        )
        project_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(WorkspaceProject)
                .where(
                    WorkspaceProject.workspace_id == workspace_id,
                    WorkspaceProject.status != "archived",
                )
            )
            or 0
        )
        return member_count, project_count

    def update_workspace_status(
        self,
        workspace_id: UUID,
        *,
        status: str,
        reason: str,
    ) -> Workspace | None:
        workspace = self._session.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        if workspace is None:
            return None
        before = workspace.status
        if before != status:
            workspace.status = status
            AuditService(self._session).record_system_action(
                workspace_id=workspace.id,
                action="platform.workspace.status_updated",
                target_type="workspace",
                target_id=workspace.id,
                metadata={"before": before, "after": status, "reason": reason},
                actor_id="platform_admin",
            )
        self._session.commit()
        self._session.refresh(workspace)
        return workspace

