from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from backend.app.core.db.base import Base
from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.platform.admin.base import AdminSessionService
from backend.app.identity.authorization.models import ResourceGrant, SecuredResource
from backend.app.identity.authorization.resources import (
    RESOURCE_TABLES,
    ResourceAction,
    ResourceKind,
)
from backend.app.identity.users.models import User
from backend.app.observability.audit.models import AuditEvent
from backend.app.observability.audit.service import AuditService
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember
from backend.app.workspaces.projects.models import WorkspaceProject


class AdminResourceAuthorizationService(AdminSessionService):
    def _resource_exists(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
    ) -> bool:
        table = Base.metadata.tables.get(RESOURCE_TABLES[kind])
        if table is None or "workspace_id" not in table.c or "id" not in table.c:
            return False
        return self._session.scalar(
            select(table.c.id).where(
                table.c.workspace_id == workspace_id,
                table.c.id == resource_id,
            )
        ) is not None

    def _locked_resource(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        *,
        create: bool = True,
    ) -> SecuredResource | None:
        if not self._resource_exists(workspace_id, kind, resource_id):
            return None
        resource = self._session.scalar(
            select(SecuredResource)
            .where(
                SecuredResource.workspace_id == workspace_id,
                SecuredResource.resource_kind == kind.value,
                SecuredResource.resource_id == resource_id,
            )
            .with_for_update()
        )
        if resource is None and create:
            resource = SecuredResource(
                workspace_id=workspace_id,
                resource_kind=kind.value,
                resource_id=resource_id,
            )
            self._session.add(resource)
            self._session.flush()
        return resource

    def get_authorization(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
    ) -> tuple[SecuredResource, list[tuple[UUID, list[ResourceAction]]]] | None:
        if not self._resource_exists(workspace_id, kind, resource_id):
            return None
        resource = self._session.scalar(
            select(SecuredResource).where(
                SecuredResource.workspace_id == workspace_id,
                SecuredResource.resource_kind == kind.value,
                SecuredResource.resource_id == resource_id,
            )
        )
        if resource is None:
            resource = SecuredResource(
                workspace_id=workspace_id,
                resource_kind=kind.value,
                resource_id=resource_id,
            )
        grants = self._session.scalars(
            select(ResourceGrant)
            .where(
                ResourceGrant.workspace_id == workspace_id,
                ResourceGrant.resource_kind == kind.value,
                ResourceGrant.resource_id == resource_id,
            )
            .order_by(ResourceGrant.user_id, ResourceGrant.action)
        ).all()
        grouped: dict[UUID, list[ResourceAction]] = {}
        for grant in grants:
            grouped.setdefault(grant.user_id, []).append(ResourceAction(grant.action))
        return resource, sorted(grouped.items(), key=lambda item: str(item[0]))

    def assign_owner(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        user_id: UUID,
    ) -> SecuredResource | None:
        member = self._session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        if member is None:
            raise ValueError("Active workspace member required")
        resource = self._locked_resource(workspace_id, kind, resource_id)
        if resource is None:
            return None
        previous = resource.owner_user_id
        resource.owner_user_id = user_id
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.resource.owner_assigned",
            target_type=kind.value,
            target_id=resource_id,
            metadata={
                "previous_owner_user_id": str(previous) if previous else None,
                "owner_user_id": str(user_id),
            },
            actor_id="platform_admin",
        )
        self._session.commit()
        self._session.refresh(resource)
        return resource

    def replace_grants(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        user_id: UUID,
        actions: frozenset[ResourceAction],
    ) -> bool:
        member = self._session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        if member is None:
            raise ValueError("Active workspace member required")
        resource = self._locked_resource(workspace_id, kind, resource_id)
        if resource is None:
            return False
        current = self._session.scalars(
            select(ResourceGrant).where(
                ResourceGrant.workspace_id == workspace_id,
                ResourceGrant.resource_kind == kind.value,
                ResourceGrant.resource_id == resource_id,
                ResourceGrant.user_id == user_id,
            )
        ).all()
        for grant in current:
            self._session.delete(grant)
        self._session.flush()
        for action in sorted(actions, key=lambda item: item.value):
            self._session.add(
                ResourceGrant(
                    workspace_id=workspace_id,
                    resource_kind=kind.value,
                    resource_id=resource_id,
                    user_id=user_id,
                    action=action.value,
                )
            )
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.resource.grants_replaced",
            target_type=kind.value,
            target_id=resource_id,
            metadata={
                "subject_user_id": str(user_id),
                "actions": sorted(action.value for action in actions),
            },
            actor_id="platform_admin",
        )
        self._session.commit()
        return True


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
        workspace = self.get_workspace(workspace_id)
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


class AdminSystemLogService(AdminSessionService):
    def list_audit_events(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        actor_type: str | None = None,
        target_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[AuditEvent], int]:
        statement = select(AuditEvent)
        if workspace_id is not None:
            statement = statement.where(AuditEvent.workspace_id == workspace_id)
        if user_id is not None:
            statement = statement.where(AuditEvent.user_id == user_id)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if actor_type is not None:
            statement = statement.where(AuditEvent.actor_type == actor_type)
        if target_type is not None:
            statement = statement.where(AuditEvent.target_type == target_type)
        if created_after is not None:
            statement = statement.where(AuditEvent.created_at >= created_after)
        if created_before is not None:
            statement = statement.where(AuditEvent.created_at <= created_before)
        statement = AuditService(self._session).apply_retention_to_statement(statement)
        ordered = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        return self._page(ordered, page)
