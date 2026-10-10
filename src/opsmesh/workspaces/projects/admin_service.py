from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from opsmesh.governance.audit.service import AuditService
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.db.read_service import AdminSessionService
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.projects.models import WorkspaceProject


class AdminWorkspaceProjectService(AdminSessionService):
    def list_projects(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[WorkspaceProject], int]:
        statement = select(WorkspaceProject).where(
            WorkspaceProject.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(WorkspaceProject.status == status)
        return page_scalars(
            self._session,
            statement.order_by(WorkspaceProject.created_at.desc()),
            page,
        )

    def get_project(self, workspace_id: UUID, project_id: UUID) -> WorkspaceProject | None:
        return self._session.scalar(
            select(WorkspaceProject).where(
                WorkspaceProject.workspace_id == workspace_id,
                WorkspaceProject.id == project_id,
            )
        )

    def update_project_status(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        status: str,
        reason: str,
    ) -> WorkspaceProject | None:
        project = self._session.scalar(
            select(WorkspaceProject)
            .where(
                WorkspaceProject.workspace_id == workspace_id,
                WorkspaceProject.id == project_id,
            )
            .with_for_update()
        )
        if project is None:
            return None
        before = project.status
        if before != status:
            project.status = status
            AuditService(self._session).record_system_action(
                workspace_id=workspace_id,
                action="platform.workspace.project_status_updated",
                target_type="workspace_project",
                target_id=project.id,
                metadata={"before": before, "after": status, "reason": reason},
                actor_id="platform_admin",
            )
        self._session.commit()
        self._session.refresh(project)
        return project

