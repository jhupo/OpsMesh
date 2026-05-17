from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.workspaces import WorkspaceCreateRequest, WorkspaceUpdateRequest
from backend.app.auth.permissions import WorkspaceRole
from backend.app.workspaces.models import Workspace, WorkspaceMember

T = TypeVar("T")


class WorkspaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_user(self, user_id: UUID, page: PageParams) -> tuple[list[Workspace], int]:
        statement = (
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active")
            .order_by(Workspace.created_at.desc())
        )
        return self._page(statement, page)

    def create_for_owner(self, owner_user_id: UUID, data: WorkspaceCreateRequest) -> Workspace:
        workspace = Workspace(
            owner_user_id=owner_user_id,
            name=data.name,
            slug=data.slug,
            settings=data.settings,
        )
        membership = WorkspaceMember(
            workspace=workspace,
            user_id=owner_user_id,
            role=WorkspaceRole.OWNER.value,
        )
        self._session.add_all([workspace, membership])
        self._session.commit()
        self._session.refresh(workspace)
        return workspace

    def get_scoped(self, workspace_id: UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def update(self, workspace: Workspace, data: WorkspaceUpdateRequest) -> Workspace:
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(workspace, field, value)
        self._session.commit()
        self._session.refresh(workspace)
        return workspace

    def list_members(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceMember], int]:
        statement = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at.desc())
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
