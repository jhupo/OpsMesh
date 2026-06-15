from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.workspaces import WorkspaceCreateRequest, WorkspaceUpdateRequest
from backend.app.api.services.workspace_invites import WorkspaceInviteService
from backend.app.api.services.workspace_settings import (
    scheduler_settings,
    semantic_resource_review_settings,
    validate_resource_review_settings,
)
from backend.app.audit.service import AuditService
from backend.app.auth.permissions import WorkspaceRole
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceQuota,
)

T = TypeVar("T")


class WorkspaceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

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
        commit_or_raise_conflict(self._session, "Workspace slug already exists")
        self._session.refresh(workspace)
        return workspace

    def get_scoped(self, workspace_id: UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def get_owned(self, owner_user_id: UUID, workspace_id: UUID) -> Workspace | None:
        return self._session.scalar(
            select(Workspace).where(
                Workspace.owner_user_id == owner_user_id,
                Workspace.id == workspace_id,
            )
        )

    def update(
        self,
        workspace: Workspace,
        data: WorkspaceUpdateRequest,
        *,
        actor_user_id: UUID | None = None,
    ) -> Workspace:
        old_status = workspace.status
        old_scheduler = scheduler_settings(workspace.settings)
        old_resource_review = semantic_resource_review_settings(workspace.settings)
        updates = data.model_dump(exclude_unset=True)
        if "settings" in updates:
            validate_resource_review_settings(
                self._session,
                workspace_id=workspace.id,
                settings=updates["settings"],
            )
        for field, value in updates.items():
            setattr(workspace, field, value)
        new_scheduler = scheduler_settings(workspace.settings)
        new_resource_review = semantic_resource_review_settings(workspace.settings)
        if actor_user_id is not None:
            audit = AuditService(self._session)
            if workspace.status != old_status:
                audit.record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.status_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_status, "after": workspace.status},
                )
            if new_scheduler != old_scheduler:
                audit.record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.scheduler_policy_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_scheduler, "after": new_scheduler},
                )
            if new_resource_review != old_resource_review:
                audit.record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.resource_review_policy_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_resource_review, "after": new_resource_review},
                )
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

    def list_invites(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceInvite], int]:
        expired_count = WorkspaceInviteService(
            self._session,
            self._settings,
        ).expire_workspace_invites(workspace_id)
        statement = (
            select(WorkspaceInvite)
            .where(WorkspaceInvite.workspace_id == workspace_id)
            .order_by(WorkspaceInvite.created_at.desc())
        )
        items, total = self._page(statement, page)
        if expired_count:
            self._session.commit()
        return items, total

    def list_quotas(self, workspace_id: UUID, page: PageParams) -> tuple[list[WorkspaceQuota], int]:
        statement = (
            select(WorkspaceQuota)
            .where(WorkspaceQuota.workspace_id == workspace_id)
            .order_by(WorkspaceQuota.status, WorkspaceQuota.quota_key)
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
