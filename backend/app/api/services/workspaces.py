from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceQuotaUpsertRequest,
    WorkspaceUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.auth.permissions import WorkspaceRole
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.workspaces.models import Workspace, WorkspaceMember, WorkspaceQuota

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
        old_scheduler = _scheduler_settings(workspace.settings)
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(workspace, field, value)
        new_scheduler = _scheduler_settings(workspace.settings)
        if actor_user_id is not None:
            if workspace.status != old_status:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.status_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_status, "after": workspace.status},
                )
            if new_scheduler != old_scheduler:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.scheduler_policy_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_scheduler, "after": new_scheduler},
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

    def list_quotas(self, workspace_id: UUID, page: PageParams) -> tuple[list[WorkspaceQuota], int]:
        statement = (
            select(WorkspaceQuota)
            .where(WorkspaceQuota.workspace_id == workspace_id)
            .order_by(WorkspaceQuota.status, WorkspaceQuota.quota_key)
        )
        return self._page(statement, page)

    def upsert_quotas(
        self,
        workspace_id: UUID,
        data: WorkspaceQuotaUpsertRequest,
    ) -> list[WorkspaceQuota]:
        existing = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota)
                .where(WorkspaceQuota.workspace_id == workspace_id)
                .with_for_update()
            ).all()
        }
        updated: list[WorkspaceQuota] = []
        for item in data.quotas:
            quota = existing.get(item.quota_key)
            if quota is None:
                quota = WorkspaceQuota(
                    workspace_id=workspace_id,
                    quota_key=item.quota_key,
                    limit_value=item.limit_value,
                    unit=item.unit,
                )
                self._session.add(quota)
            else:
                quota.limit_value = item.limit_value
                quota.unit = item.unit
                quota.status = "active"
            updated.append(quota)
        self._session.commit()
        for quota in updated:
            self._session.refresh(quota)
        return sorted(updated, key=lambda quota: quota.quota_key)

    def disable_quota(self, workspace_id: UUID, quota_key: str) -> WorkspaceQuota | None:
        quota = self._session.scalar(
            select(WorkspaceQuota)
            .where(
                WorkspaceQuota.workspace_id == workspace_id,
                WorkspaceQuota.quota_key == quota_key,
            )
            .with_for_update()
        )
        if quota is None:
            return None
        quota.status = "disabled"
        self._session.commit()
        self._session.refresh(quota)
        return quota

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)
