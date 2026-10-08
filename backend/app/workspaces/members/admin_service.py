from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from backend.app.governance.audit.service import AuditService
from backend.app.identity.users.models import User
from backend.app.shared.db.read_service import AdminSessionService
from backend.app.shared.pagination import PageParams
from backend.app.workspaces.management.admin_service import AdminWorkspaceManagementService
from backend.app.workspaces.members.models import WorkspaceMember


class AdminWorkspaceMemberService(AdminSessionService):
    def list_members(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[tuple[WorkspaceMember, User]], int]:
        statement = (
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id)
        )
        count_statement = select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(WorkspaceMember.status == status)
            count_statement = count_statement.where(WorkspaceMember.status == status)
        total = int(self._session.scalar(count_statement) or 0)
        result = self._session.execute(
            statement.order_by(WorkspaceMember.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = [(row[0], row[1]) for row in result.all()]
        return rows, total

    def update_member(
        self,
        workspace_id: UUID,
        member_id: UUID,
        *,
        role: str | None,
        status: str | None,
        reason: str,
    ) -> WorkspaceMember | None:
        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.id == member_id,
            )
            .with_for_update()
        )
        if member is None:
            return None
        before = {"role": member.role, "status": member.status}
        next_role = role if role is not None else member.role
        next_status = status if status is not None else member.status
        if member.role == "owner" and member.status == "active":
            removing_owner = next_role != "owner" or next_status != "active"
            if removing_owner:
                active_owner_count = int(
                    self._session.scalar(
                        select(func.count())
                        .select_from(WorkspaceMember)
                        .where(
                            WorkspaceMember.workspace_id == workspace_id,
                            WorkspaceMember.role == "owner",
                            WorkspaceMember.status == "active",
                        )
                    )
                    or 0
                )
                if active_owner_count <= 1:
                    raise ValueError("Cannot remove or downgrade the last workspace owner")
        member.role = next_role
        member.status = next_status
        after = {"role": member.role, "status": member.status}
        if before != after:
            AuditService(self._session).record_system_action(
                workspace_id=workspace_id,
                action="platform.workspace.member_updated",
                target_type="workspace_member",
                target_id=member.id,
                metadata={
                    "target_user_id": str(member.user_id),
                    "before": before,
                    "after": after,
                    "reason": reason,
                },
                actor_id="platform_admin",
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def add_member(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        role: str,
        reason: str,
    ) -> WorkspaceMember | None:
        workspace = AdminWorkspaceManagementService(self._session).get_workspace(workspace_id)
        user = self._session.get(User, user_id)
        if workspace is None or user is None or user.status != "active":
            return None
        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
            .with_for_update()
        )
        if member is None:
            member = WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
                status="active",
            )
            self._session.add(member)
            self._session.flush()
        else:
            member.role = role
            member.status = "active"
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.workspace.member_added",
            target_type="workspace_member",
            target_id=member.id,
            metadata={"target_user_id": str(user_id), "role": role, "reason": reason},
            actor_id="platform_admin",
        )
        self._session.commit()
        self._session.refresh(member)
        return member

    def remove_member(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        reason: str,
    ) -> WorkspaceMember | None:
        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
            .with_for_update()
        )
        if member is None:
            return None
        if member.role == "owner" and member.status == "active":
            owners = int(
                self._session.scalar(
                    select(func.count())
                    .select_from(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.role == "owner",
                        WorkspaceMember.status == "active",
                    )
                )
                or 0
            )
            if owners <= 1:
                raise ValueError("Cannot remove the last workspace owner")
        member.status = "disabled"
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.workspace.member_removed",
            target_type="workspace_member",
            target_id=member.id,
            metadata={"target_user_id": str(user_id), "reason": reason},
            actor_id="platform_admin",
        )
        self._session.commit()
        self._session.refresh(member)
        return member

