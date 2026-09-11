from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.workspaces import (
    WorkspaceMemberCreateRequest,
    WorkspaceMemberUpdateRequest,
)
from backend.app.api.services.workspace_errors import (
    WorkspaceMemberConflictError,
    WorkspaceMemberNotFoundError,
    WorkspaceMemberPermissionError,
)
from backend.app.api.services.workspace_snapshots import member_snapshot
from backend.app.observability.audit_service import AuditService
from backend.app.auth.permissions import WorkspaceRole
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.identity.models import User
from backend.app.workspaces.models import WorkspaceMember


class WorkspaceMemberService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_member(
        self,
        workspace_id: UUID,
        data: WorkspaceMemberCreateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        ensure_actor_can_assign_role(actor_role, data.role)
        user = self._session.get(User, data.user_id)
        if user is None or user.status != "active":
            raise WorkspaceMemberNotFoundError("User not found")

        existing = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == data.user_id,
            )
            .with_for_update()
        )
        if existing is not None and existing.status == "active":
            raise WorkspaceMemberConflictError("Workspace member already exists")

        before: dict[str, object] | None
        if existing is None:
            member = WorkspaceMember(
                workspace_id=workspace_id,
                user_id=data.user_id,
                role=data.role,
                status="active",
            )
            self._session.add(member)
            self._session.flush([member])
            before = None
        else:
            member = existing
            before = member_snapshot(member)
            member.role = data.role
            member.status = "active"

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="workspace.member_created",
            target_type="workspace_member",
            target_id=member.id,
            metadata={
                "target_user_id": str(member.user_id),
                "before": before,
                "after": member_snapshot(member),
            },
        )
        commit_or_raise_conflict(self._session, "Workspace member already exists")
        self._session.refresh(member)
        return member

    def update_member(
        self,
        workspace_id: UUID,
        member_id: UUID,
        data: WorkspaceMemberUpdateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        member = self.get_member_for_update(workspace_id, member_id)
        before = member_snapshot(member)
        new_role = data.role if data.role is not None else member.role
        new_status = data.status if data.status is not None else member.status
        ensure_actor_can_manage_member_role(actor_role, member, new_role)
        self._ensure_owner_can_change(member, new_role=new_role, new_status=new_status)

        member.role = new_role
        member.status = new_status
        after = member_snapshot(member)
        if after != before:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.member_updated",
                target_type="workspace_member",
                target_id=member.id,
                metadata={
                    "target_user_id": str(member.user_id),
                    "before": before,
                    "after": after,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def disable_member(
        self,
        workspace_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        member = self.get_member_for_update(workspace_id, member_id)
        before = member_snapshot(member)
        ensure_actor_can_manage_member_role(actor_role, member, member.role)
        self._ensure_owner_can_change(
            member,
            new_role=member.role,
            new_status="disabled",
        )

        member.status = "disabled"
        after = member_snapshot(member)
        if after != before:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.member_disabled",
                target_type="workspace_member",
                target_id=member.id,
                metadata={
                    "target_user_id": str(member.user_id),
                    "before": before,
                    "after": after,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def get_member_for_update(self, workspace_id: UUID, member_id: UUID) -> WorkspaceMember:
        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.id == member_id,
            )
            .with_for_update()
        )
        if member is None:
            raise WorkspaceMemberNotFoundError("Workspace member not found")
        return member

    def _ensure_owner_can_change(
        self,
        member: WorkspaceMember,
        *,
        new_role: str,
        new_status: str,
    ) -> None:
        remains_active_owner = new_role == WorkspaceRole.OWNER.value and new_status == "active"
        if (
            member.role != WorkspaceRole.OWNER.value
            or member.status != "active"
            or remains_active_owner
        ):
            return

        active_owner_count = self._session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == member.workspace_id,
                WorkspaceMember.role == WorkspaceRole.OWNER.value,
                WorkspaceMember.status == "active",
            )
        )
        if int(active_owner_count or 0) <= 1:
            raise WorkspaceMemberConflictError(
                "Cannot remove or downgrade the last workspace owner"
            )


def ensure_actor_can_assign_role(actor_role: str, target_role: str) -> None:
    if target_role == WorkspaceRole.OWNER.value and actor_role != WorkspaceRole.OWNER.value:
        raise WorkspaceMemberPermissionError("Only workspace owners can grant owner role")


def ensure_actor_can_manage_member_role(
    actor_role: str,
    member: WorkspaceMember,
    new_role: str,
) -> None:
    if actor_role == WorkspaceRole.OWNER.value:
        return
    if member.role == WorkspaceRole.OWNER.value:
        raise WorkspaceMemberPermissionError("Only workspace owners can manage owners")
    ensure_actor_can_assign_role(actor_role, new_role)
