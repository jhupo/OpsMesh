from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from backend.app.governance.audit.service import AuditService
from backend.app.identity.authorization.models import ResourceGrant, SecuredResource
from backend.app.identity.authorization.resources import (
    RESOURCE_TABLES,
    ResourceAction,
    ResourceKind,
)
from backend.app.shared.db.base import Base
from backend.app.shared.db.read_service import AdminSessionService
from backend.app.workspaces.members.models import WorkspaceMember


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
