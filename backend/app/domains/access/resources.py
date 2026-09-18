"""Product resource authorization; membership and token scopes remain upper bounds."""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, false, or_, select
from sqlalchemy.orm import Session

from backend.app.core.db.base import Base
from backend.app.core.errors import DomainError
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.errors import AuthorizationError
from backend.app.domains.access.models import ResourceGrant, SecuredResource, User
from backend.app.domains.access.permissions import WorkspaceAction, WorkspaceRole, role_allows
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember
from backend.app.observability.audit.service import AuditService


class ResourceAction(StrEnum):
    READ = "read"
    INVOKE = "invoke"
    UPDATE = "update"
    DELETE = "delete"
    SHARE = "share"
    CONTROL = "control"
    APPROVE = "approve"


class ResourceKind(StrEnum):
    TEAM = "team"
    AGENT = "agent"
    PROJECT = "project"
    DOMAIN_PROJECT = "domain_project"
    DOMAIN_ITEM = "domain_item"
    TASK = "task"
    CAPABILITY = "capability"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    SESSION = "session"
    THREAD = "thread"
    WORKFLOW = "workflow"
    FILE = "file"
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    AUTOMATION = "automation"
    RUNTIME_SPACE = "runtime_space"
    RUNTIME = "runtime"
    PROVIDER = "provider"
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"


# The resource catalog is product-owned, not a caller-controlled table name or ORM path.
RESOURCE_TABLES: dict[ResourceKind, str] = {
    ResourceKind.TEAM: "agent_teams",
    ResourceKind.AGENT: "agent_profiles",
    ResourceKind.PROJECT: "workspace_projects",
    ResourceKind.DOMAIN_PROJECT: "domain_projects",
    ResourceKind.DOMAIN_ITEM: "domain_items",
    ResourceKind.TASK: "tasks",
    ResourceKind.CAPABILITY: "capability_resources",
    ResourceKind.MCP_SERVER: "mcp_servers",
    ResourceKind.MCP_TOOL: "mcp_tool_allowlist",
    ResourceKind.SKILL: "workspace_skill_installs",
    ResourceKind.SESSION: "persistent_agent_sessions",
    ResourceKind.THREAD: "agent_message_threads",
    ResourceKind.WORKFLOW: "orchestration_definitions",
    ResourceKind.FILE: "workspace_files",
    ResourceKind.KNOWLEDGE: "knowledge_sources",
    ResourceKind.MEMORY: "workspace_memory_entries",
    ResourceKind.AUTOMATION: "automations",
    ResourceKind.RUNTIME_SPACE: "runtime_spaces",
    ResourceKind.RUNTIME: "workspace_runtimes",
    ResourceKind.PROVIDER: "model_provider_credentials",
    ResourceKind.WEBHOOK: "webhook_subscriptions",
    ResourceKind.SCHEDULE: "workspace_scheduled_jobs",
}

_WORKSPACE_ACTION = {
    ResourceAction.READ: WorkspaceAction.READ,
    ResourceAction.INVOKE: WorkspaceAction.WRITE,
    ResourceAction.UPDATE: WorkspaceAction.WRITE,
    ResourceAction.DELETE: WorkspaceAction.WRITE,
    ResourceAction.SHARE: WorkspaceAction.WRITE,
    ResourceAction.CONTROL: WorkspaceAction.OPERATE,
    ResourceAction.APPROVE: WorkspaceAction.APPROVE,
}


class ResourceAccessDenied(DomainError):
    def __init__(self) -> None:
        super().__init__(
            message="Resource access denied",
            code="resource_access_denied",
            status_code=403,
        )


class ResourceAuthorizationService:
    def __init__(self, session: Session, user: AuthenticatedUser) -> None:
        self._session = session
        self.user = user

    def predicate(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: ColumnElement[UUID],
        action: ResourceAction = ResourceAction.READ,
    ) -> ColumnElement[bool]:
        """Return a SQL predicate, to apply before count, sort, limit and retrieval."""
        workspace_action = _WORKSPACE_ACTION[action]
        if not self.user.allows_workspace_action(workspace_id, workspace_action):
            return false()
        member = WorkspaceMember.__table__.alias("resource_member")
        workspace = Workspace.__table__.alias("resource_workspace")
        user = User.__table__.alias("resource_user")
        secured = SecuredResource.__table__.alias("authorized_resource")
        grant = ResourceGrant.__table__.alias("authorized_grant")
        permitted = or_(
            secured.c.owner_user_id == self.user.user_id,
            exists(
                select(grant.c.resource_id)
                .where(
                    grant.c.workspace_id == workspace_id,
                    grant.c.resource_kind == kind.value,
                    grant.c.resource_id == secured.c.resource_id,
                    grant.c.user_id == self.user.user_id,
                    grant.c.action == action.value,
                )
                .correlate_except(grant)
            ),
        )
        resource_permission: ColumnElement[bool] = exists(
            select(secured.c.resource_id)
            .where(
                secured.c.workspace_id == workspace_id,
                secured.c.resource_kind == kind.value,
                secured.c.resource_id == resource_id,
                permitted,
            )
            .correlate_except(secured)
        )
        if action == ResourceAction.READ:
            resource_permission = or_(
                resource_permission,
                self._inherited_read(workspace_id, kind, resource_id),
            )
        return exists(
            select(member.c.id)
            .select_from(member)
            .join(
                workspace,
                workspace.c.id == member.c.workspace_id,
            )
            .join(user, user.c.id == member.c.user_id)
            .where(
                member.c.workspace_id == workspace_id,
                member.c.user_id == self.user.user_id,
                member.c.status == "active",
                workspace.c.status == "active",
                user.c.status == "active",
                member.c.role.in_(
                    [role.value for role in WorkspaceRole if role_allows(role, workspace_action)]
                ),
                or_(member.c.role.in_(["owner", "admin"]), resource_permission),
            )
            .correlate_except(member, workspace, user)
        )

    def _inherited_read(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: ColumnElement[UUID],
    ) -> ColumnElement[bool]:
        if kind == ResourceKind.DOMAIN_ITEM:
            items = Base.metadata.tables["domain_items"].alias("domain_item_access")
            return exists(
                select(items.c.id)
                .where(
                    items.c.workspace_id == workspace_id,
                    items.c.id == resource_id,
                    or_(
                        self.predicate(workspace_id, ResourceKind.TASK, items.c.task_id),
                        self.predicate(
                            workspace_id, ResourceKind.DOMAIN_PROJECT, items.c.domain_project_id
                        ),
                    ),
                )
                .correlate_except(items)
            )
        if kind == ResourceKind.THREAD:
            threads = Base.metadata.tables["agent_message_threads"].alias("thread_task_access")
            return exists(
                select(threads.c.id)
                .where(
                    threads.c.workspace_id == workspace_id,
                    threads.c.id == resource_id,
                    self.predicate(workspace_id, ResourceKind.TASK, threads.c.task_id),
                )
                .correlate_except(threads)
            )
        if kind == ResourceKind.FILE:
            links = Base.metadata.tables["workspace_project_files"].alias("file_project_access")
            return exists(
                select(links.c.id)
                .where(
                    links.c.workspace_id == workspace_id,
                    links.c.workspace_file_id == resource_id,
                    self.predicate(workspace_id, ResourceKind.PROJECT, links.c.project_id),
                )
                .correlate_except(links)
            )
        if kind == ResourceKind.MEMORY:
            citations = Base.metadata.tables["knowledge_citations"].alias("memory_source_access")
            entries = Base.metadata.tables["workspace_memory_entries"].alias("memory_run_access")
            runs = Base.metadata.tables["agent_runs"].alias("memory_owner_run")
            return or_(
                exists(
                    select(citations.c.id)
                    .where(
                        citations.c.workspace_id == workspace_id,
                        citations.c.memory_entry_id == resource_id,
                        self.predicate(workspace_id, ResourceKind.KNOWLEDGE, citations.c.source_id),
                    )
                    .correlate_except(citations)
                ),
                exists(
                    select(entries.c.id)
                    .select_from(entries)
                    .join(
                        runs,
                        runs.c.id == entries.c.created_by_agent_run_id,
                    )
                    .where(
                        entries.c.workspace_id == workspace_id,
                        runs.c.workspace_id == workspace_id,
                        entries.c.id == resource_id,
                        self.predicate(workspace_id, ResourceKind.TASK, runs.c.task_id),
                    )
                    .correlate_except(entries, runs)
                ),
            )
        return false()

    def require(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        action: ResourceAction,
    ) -> None:
        self.require_many(workspace_id, [(kind, resource_id, action)])

    def require_many(
        self,
        workspace_id: UUID,
        references: list[tuple[ResourceKind, UUID, ResourceAction]],
    ) -> None:
        try:
            live = AuthorizationService(self._session).refresh_authenticated_user(self.user)
            if live.token_scopes != self.user.token_scopes:
                raise ResourceAccessDenied()
        except AuthorizationError as exc:
            raise ResourceAccessDenied() from exc
        clauses = []
        for kind, resource_id, action in references:
            table = Base.metadata.tables[RESOURCE_TABLES[kind]]
            permitted = self.predicate(workspace_id, kind, table.c.id, action)
            clauses.append(
                exists(
                    select(table.c.id).where(
                        table.c.workspace_id == workspace_id,
                        table.c.id == resource_id,
                        permitted,
                    )
                )
            )
        if clauses and not self._session.scalar(select(and_(*clauses))):
            raise ResourceAccessDenied()

    def effective_actions(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
    ) -> list[ResourceAction]:
        self.require(workspace_id, kind, resource_id, ResourceAction.READ)
        table = Base.metadata.tables[RESOURCE_TABLES[kind]]
        row = self._session.execute(
            select(
                *(
                    exists(
                        select(table.c.id).where(
                            table.c.workspace_id == workspace_id,
                            table.c.id == resource_id,
                            self.predicate(workspace_id, kind, table.c.id, action),
                        )
                    ).label(action.value)
                    for action in ResourceAction
                )
            )
        ).one()
        return [action for action in ResourceAction if row._mapping[action.value]]

    def list_grants(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
    ) -> dict[UUID, list[ResourceAction]]:
        self.require(workspace_id, kind, resource_id, ResourceAction.SHARE)
        grants = self._session.scalars(
            select(ResourceGrant)
            .where(
                ResourceGrant.workspace_id == workspace_id,
                ResourceGrant.resource_kind == kind.value,
                ResourceGrant.resource_id == resource_id,
            )
            .order_by(ResourceGrant.user_id, ResourceGrant.action)
        )
        grouped: dict[UUID, list[ResourceAction]] = {}
        for grant in grants:
            grouped.setdefault(grant.user_id, []).append(ResourceAction(grant.action))
        return grouped

    def replace_grants(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        user_id: UUID,
        actions: frozenset[ResourceAction],
    ) -> None:
        self.require(workspace_id, kind, resource_id, ResourceAction.SHARE)
        resource = self._session.scalar(
            select(SecuredResource)
            .where(
                SecuredResource.workspace_id == workspace_id,
                SecuredResource.resource_kind == kind.value,
                SecuredResource.resource_id == resource_id,
            )
            .with_for_update()
        )
        if resource is None:
            raise ResourceAccessDenied()
        self.require(workspace_id, kind, resource_id, ResourceAction.SHARE)
        member = self._session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        if member is None:
            raise ResourceAccessDenied()
        # Delegation cannot manufacture actions the granting principal does not possess.
        for action in actions:
            self.require(workspace_id, kind, resource_id, action)
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
        for action in sorted(actions):
            self._session.add(
                ResourceGrant(
                    workspace_id=workspace_id,
                    resource_kind=kind.value,
                    resource_id=resource_id,
                    user_id=user_id,
                    action=action.value,
                )
            )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=self.user.user_id,
            action="resource.grants.replaced",
            target_type=kind.value,
            target_id=resource_id,
            metadata={"subject_user_id": str(user_id), "actions": sorted(actions)},
        )
        self._session.flush()

    def assign_owner(
        self,
        workspace_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        user_id: UUID,
    ) -> None:
        auth = AuthorizationService(self._session)
        auth.require_workspace(
            user_id=self.user.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=self.user,
        )
        auth.require_workspace(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.READ
        )
        self.require(workspace_id, kind, resource_id, ResourceAction.SHARE)
        resource = self._session.scalar(
            select(SecuredResource)
            .where(
                SecuredResource.workspace_id == workspace_id,
                SecuredResource.resource_kind == kind.value,
                SecuredResource.resource_id == resource_id,
            )
            .with_for_update()
        )
        previous = resource.owner_user_id if resource is not None else None
        if resource is None:
            resource = SecuredResource(
                workspace_id=workspace_id,
                resource_kind=kind.value,
                resource_id=resource_id,
                owner_user_id=user_id,
            )
            self._session.add(resource)
        resource.owner_user_id = user_id
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=self.user.user_id,
            action="resource.owner.assigned",
            target_type=kind.value,
            target_id=resource_id,
            metadata={
                "previous_owner_user_id": str(previous) if previous else None,
                "owner_user_id": str(user_id),
            },
        )
        self._session.flush()
