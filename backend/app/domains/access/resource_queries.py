"""Request-scoped ORM visibility and ownership, using SQLAlchemy's public events.

Only API/user execution sessions are bound. Maintenance sessions must use explicit service
authorization when accepting work; their database access is not a user's read capability.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Column, ColumnElement, Table, and_, event, exists, inspect, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.sql.visitors import iterate

from backend.app.core.db.base import Base
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.models import SecuredResource
from backend.app.domains.access.resources import (
    RESOURCE_TABLES,
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)

_ROOTS = {table: kind for kind, table in RESOURCE_TABLES.items()}

# A dependent row inherits data access from its owning aggregate, never from an unrelated
# agent/tool reference. A team grant deliberately does not grant access to its users' tasks.
_PARENTS: dict[str, tuple[str, str]] = {
    "workspace_agent_installs": ("installed_agent_profile_id", "agent_profiles"),
    "webhook_delivery_attempts": ("subscription_id", "webhook_subscriptions"),
    "agent_profile_versions": ("agent_profile_id", "agent_profiles"),
    "agent_team_members": ("agent_team_id", "agent_teams"),
    "orchestration_revisions": ("definition_id", "orchestration_definitions"),
    "workspace_project_configuration_versions": ("project_id", "workspace_projects"),
    "workspace_project_files": ("project_id", "workspace_projects"),
    "workspace_project_outputs": ("project_id", "workspace_projects"),
    "knowledge_source_ingestions": ("source_id", "knowledge_sources"),
    "knowledge_source_revisions": ("source_id", "knowledge_sources"),
    "knowledge_citations": ("source_id", "knowledge_sources"),
    "workspace_memory_versions": ("memory_entry_id", "workspace_memory_entries"),
    "workspace_memory_embedding_events": ("memory_entry_id", "workspace_memory_entries"),
    "workspace_memory_lifecycle_events": ("memory_entry_id", "workspace_memory_entries"),
    "task_steps": ("task_id", "tasks"),
    "task_transfers": ("task_id", "tasks"),
    "task_messages": ("task_id", "tasks"),
    "task_event_outbox": ("task_id", "tasks"),
    "task_planning_attempts": ("task_id", "tasks"),
    "agent_runs": ("task_id", "tasks"),
    "approvals": ("task_id", "tasks"),
    "pending_tool_invocations": ("task_id", "tasks"),
    "review_comments": ("task_id", "tasks"),
    "revision_requests": ("task_id", "tasks"),
    "artifacts": ("task_id", "tasks"),
    "agent_messages": ("thread_id", "agent_message_threads"),
    "persistent_agent_session_items": ("persistent_session_id", "persistent_agent_sessions"),
    "subworkflow_invocations": ("parent_task_id", "tasks"),
    "automation_events": ("task_id", "tasks"),
    "run_events": ("agent_run_id", "agent_runs"),
    "agent_run_state_snapshots": ("agent_run_id", "agent_runs"),
    "agent_run_project_snapshots": ("agent_run_id", "agent_runs"),
    "agent_run_project_io_states": ("agent_run_id", "agent_runs"),
    "model_usage_records": ("task_id", "tasks"),
    "mcp_tool_call_logs": ("task_id", "tasks"),
    "workspace_scheduled_job_events": ("scheduled_job_id", "workspace_scheduled_jobs"),
    "runtime_space_bindings": ("runtime_space_id", "runtime_spaces"),
    "runtime_space_events": ("runtime_space_id", "runtime_spaces"),
    "runtime_space_quotas": ("runtime_space_id", "runtime_spaces"),
    "runtime_commands": ("workspace_runtime_id", "workspace_runtimes"),
    "runtime_events": ("workspace_runtime_id", "workspace_runtimes"),
    "runtime_leases": ("workspace_runtime_id", "workspace_runtimes"),
    "local_file_references": ("task_id", "tasks"),
    "file_access_events": ("workspace_file_id", "workspace_files"),
}

# These are already governed by workspace-role or dedicated control-plane policy. Unknown
# workspace tables are administrator-only until an explicit data ownership rule is added.
_WORKSPACE_METADATA = frozenset(
    {
        "workspace_members",
        "workspace_invites",
        "workspace_quotas",
        "workspace_reservations",
        "secured_resources",
        "resource_grants",
        "external_identity_bindings",
    }
)


@dataclass(frozen=True)
class ResourceQueryScope:
    workspace_id: UUID
    user: AuthenticatedUser
    mutation_action: ResourceAction = ResourceAction.UPDATE
    execution: bool = False


def bind_resource_queries(session: Session, scope: ResourceQueryScope) -> None:
    current = session.info.get("resource_query_scope")
    if current is not None and current != scope:
        raise ValueError("A database session cannot change its resource principal")
    if current is not None:
        return
    session.info["resource_query_scope"] = scope
    event.listen(session, "do_orm_execute", _filter_queries)
    event.listen(session, "before_flush", _register_ownership)
    if not scope.execution:
        event.listen(session, "before_flush", _authorize_changes)


def resource_query_scope(session: Session) -> ResourceQueryScope | None:
    value = session.info.get("resource_query_scope")
    return value if isinstance(value, ResourceQueryScope) else None


def require_resource_row(
    session: Session,
    scope: ResourceQueryScope,
    table_name: str,
    resource_id: UUID,
    action: ResourceAction,
) -> None:
    table = Base.metadata.tables[table_name]
    if (
        session.connection().scalar(
            select(table.c.id).where(
                table.c.id == resource_id,
                _row_predicate(session, scope, table_name, action),
            )
        )
        is None
    ):
        raise ResourceAccessDenied()


def unbind_resource_queries(session: Session) -> None:
    scope = resource_query_scope(session)
    if scope is None:
        return
    event.remove(session, "do_orm_execute", _filter_queries)
    event.remove(session, "before_flush", _register_ownership)
    if not scope.execution:
        event.remove(session, "before_flush", _authorize_changes)
    session.info.pop("resource_query_scope", None)


@contextmanager
def execution_resource_queries(
    session: Session,
    workspace_id: UUID,
    user: AuthenticatedUser,
) -> Iterator[None]:
    bind_resource_queries(session, ResourceQueryScope(workspace_id, user, execution=True))
    try:
        yield
    finally:
        unbind_resource_queries(session)


def _row_predicate(
    session: Session,
    scope: ResourceQueryScope,
    table_name: str,
    action: ResourceAction = ResourceAction.READ,
) -> ColumnElement[bool]:
    table = Base.metadata.tables[table_name]
    service = ResourceAuthorizationService(session, scope.user)
    tenant = table.c.workspace_id == scope.workspace_id
    if table_name == "marketplace_listings" and action == ResourceAction.READ:
        return or_(
            and_(tenant, _workspace_admin(scope)),
            and_(table.c.visibility == "public", table.c.status == "public"),
        )
    if table_name == "agent_messages":
        return and_(
            tenant,
            or_(
                and_(
                    table.c.task_id.is_not(None),
                    service.predicate(
                        scope.workspace_id,
                        ResourceKind.TASK,
                        table.c.task_id,
                        action,
                    ),
                ),
                and_(
                    table.c.task_id.is_(None),
                    service.predicate(
                        scope.workspace_id,
                        ResourceKind.THREAD,
                        table.c.thread_id,
                        action,
                    ),
                ),
            ),
        )
    if table_name == "automation_events":
        automation = Base.metadata.tables["automations"]
        producer = exists(
            select(automation.c.id)
            .where(
                automation.c.workspace_id == scope.workspace_id,
                automation.c.id == table.c.automation_id,
                automation.c.created_by_user_id == scope.user.user_id,
            )
            .correlate_except(automation)
        )
        return and_(
            tenant,
            or_(
                producer,
                table.c.execution_identity["user_id"].as_string() == str(scope.user.user_id),
                service.predicate(scope.workspace_id, ResourceKind.TASK, table.c.task_id, action),
            ),
        )
    if table_name in {"approvals", "mcp_tool_call_logs"}:
        task_access = and_(
            table.c.task_id.is_not(None),
            service.predicate(scope.workspace_id, ResourceKind.TASK, table.c.task_id, action),
        )
        # Resource reviews and standalone MCP calls are not task-owned. A deleted task must
        # not accidentally turn its former approval into a workspace-wide approval.
        standalone_access = (
            and_(
                table.c.payload["kind"].as_string() == "resource_review",
                _workspace_admin(scope),
            )
            if table_name == "approvals"
            else and_(
                _workspace_admin(scope),
                service.predicate(
                    scope.workspace_id, ResourceKind.MCP_SERVER, table.c.mcp_server_id, action
                ),
            )
        )
        return and_(
            tenant,
            or_(task_access, and_(table.c.task_id.is_(None), standalone_access)),
        )
    if table_name in _ROOTS:
        return and_(
            tenant,
            service.predicate(
                scope.workspace_id,
                _ROOTS[table_name],
                table.c.id,
                action,
            ),
        )
    if table_name in _PARENTS:
        column, parent_name = _PARENTS[table_name]
        parent = Base.metadata.tables[parent_name]
        return and_(
            tenant,
            exists(
                select(parent.c.id)
                .where(
                    parent.c.workspace_id == table.c.workspace_id,
                    parent.c.id == table.c[column],
                    _row_predicate(session, scope, parent_name, action),
                )
                .correlate_except(parent)
            ),
        )
    if table_name in _WORKSPACE_METADATA:
        return tenant
    return and_(tenant, _workspace_admin(scope))


def _workspace_admin(scope: ResourceQueryScope) -> ColumnElement[bool]:
    members = Base.metadata.tables["workspace_members"].alias("data_admin")
    return exists(
        select(members.c.id).where(
            members.c.workspace_id == scope.workspace_id,
            members.c.user_id == scope.user.user_id,
            members.c.status == "active",
            members.c.role.in_(["owner", "admin"]),
        )
    )


def _filter_queries(state: ORMExecuteState) -> None:
    scope = resource_query_scope(state.session)
    if scope is None or not state.is_orm_statement:
        return
    action = (
        ResourceAction.DELETE
        if state.is_delete
        else scope.mutation_action
        if state.is_update
        else ResourceAction.READ
    )
    options = []
    referenced_tables = {
        node.name if isinstance(node, Table) else node.table.name
        for node in iterate(cast(ClauseElement, state.statement))
        if isinstance(node, Table) or (isinstance(node, Column) and isinstance(node.table, Table))
    }
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if "workspace_id" not in table.c:
            continue
        table_name = mapper.class_.__tablename__
        if table_name not in referenced_tables:
            continue
        if scope.execution:
            # Provider credentials, pool members and control-plane evidence are consumed by
            # trusted runtime services, not exposed as user data. Their own gateways authorize
            # the execution. Keep user-data reads constrained throughout the run.
            root = table_name
            while root in _PARENTS:
                root = _PARENTS[root][1]
            if _ROOTS.get(root) not in {
                ResourceKind.TASK,
                ResourceKind.TEAM,
                ResourceKind.AGENT,
                ResourceKind.PROJECT,
                ResourceKind.DOMAIN_PROJECT,
                ResourceKind.DOMAIN_ITEM,
                ResourceKind.FILE,
                ResourceKind.KNOWLEDGE,
                ResourceKind.MEMORY,
                ResourceKind.CAPABILITY,
                ResourceKind.MCP_TOOL,
                ResourceKind.SKILL,
                ResourceKind.SESSION,
                ResourceKind.THREAD,
                ResourceKind.WORKFLOW,
            }:
                continue
        row_action = action
        if scope.execution and _ROOTS.get(table_name) in {
            ResourceKind.AGENT,
            ResourceKind.TEAM,
            ResourceKind.CAPABILITY,
            ResourceKind.MCP_TOOL,
            ResourceKind.SKILL,
            ResourceKind.WORKFLOW,
        }:
            row_action = ResourceAction.INVOKE
        options.append(
            with_loader_criteria(
                mapper.class_,
                _row_predicate(state.session, scope, table_name, row_action),
                include_aliases=True,
                propagate_to_loaders=True,
            )
        )
    state.statement = state.statement.options(*options)


def _register_ownership(session: Session, flush_context: object, instances: object) -> None:
    scope = resource_query_scope(session)
    if scope is None:
        return
    for instance in list(session.new):
        table = inspect(type(instance)).local_table
        if table.name not in _ROOTS:
            continue
        if instance.workspace_id != scope.workspace_id:
            raise ValueError("Resource creation cannot cross the request workspace")
        identifier = instance.id
        if identifier is None:
            identifier = uuid4()
            instance.id = identifier
        policy = cast(Table, SecuredResource.__table__)
        connection = session.connection()
        exists_already = connection.scalar(
            select(policy.c.resource_id).where(
                policy.c.workspace_id == scope.workspace_id,
                policy.c.resource_kind == _ROOTS[table.name].value,
                policy.c.resource_id == identifier,
            )
        )
        if exists_already is None:
            # This uses the resource's transaction, including a caller's savepoint. A limited
            # ORM flush must not leave the new resource temporarily without its private owner.
            connection.execute(
                policy.insert().values(
                    workspace_id=scope.workspace_id,
                    resource_kind=_ROOTS[table.name].value,
                    resource_id=identifier,
                    owner_user_id=scope.user.user_id,
                )
            )


_APPENDED_EVIDENCE = frozenset(
    {
        "audit_events",
        "security_events",
        "file_access_events",
        "workspace_memory_retrieval_events",
        "workspace_memory_lifecycle_events",
        "workspace_memory_embedding_events",
    }
)


def _authorize_changes(session: Session, flush_context: object, instances: object) -> None:
    scope = resource_query_scope(session)
    if scope is None:
        return
    new_parents = {
        (inspect(type(row)).local_table.name, row.id) for row in session.new if hasattr(row, "id")
    }
    for row in set(session.dirty).union(session.deleted, session.new):
        table = inspect(type(row)).local_table
        if "workspace_id" not in table.c or table.name in _WORKSPACE_METADATA:
            continue
        if row.workspace_id != scope.workspace_id:
            raise ResourceAccessDenied()
        if row in session.new:
            if table.name in _ROOTS or table.name in _APPENDED_EVIDENCE:
                continue
            if (
                table.name == "approvals"
                and row.task_id is None
                and row.payload.get("kind") == "resource_review"
                and row.status == "pending"
                and row.decided_by_user_id is None
            ):
                # Only the authorized resource-review service appends these requests; there
                # is no client approval-creation endpoint. Deciding still requires admin.
                continue
            if table.name in _PARENTS:
                foreign_key, parent_name = _PARENTS[table.name]
                if table.name == "automation_events":
                    foreign_key, parent_name = "automation_id", "automations"
                elif table.name == "mcp_tool_call_logs" and row.task_id is None:
                    foreign_key, parent_name = "mcp_server_id", "mcp_servers"
                identifier = getattr(row, foreign_key)
                if (parent_name, identifier) in new_parents:
                    continue
                parent = Base.metadata.tables[parent_name]
                condition = and_(
                    parent.c.id == identifier,
                    _row_predicate(session, scope, parent_name, scope.mutation_action),
                )
                statement = select(parent.c.id).where(condition)
            else:
                # Control-plane creation remains administrator-owned.
                members = Base.metadata.tables["workspace_members"]
                statement = select(members.c.id).where(
                    members.c.workspace_id == scope.workspace_id,
                    members.c.user_id == scope.user.user_id,
                    members.c.status == "active",
                    members.c.role.in_(["owner", "admin"]),
                )
        else:
            if row not in session.deleted and not session.is_modified(
                row,
                include_collections=False,
            ):
                continue
            action = ResourceAction.DELETE if row in session.deleted else scope.mutation_action
            statement = select(table.c.id).where(
                table.c.id == row.id,
                _row_predicate(session, scope, table.name, action),
            )
        # Core table expressions avoid recursive ORM loading during flush; no commit or bypass
        # is exposed to the caller. This remains the same database transaction.
        if session.connection().scalar(statement) is None:
            raise ResourceAccessDenied()
