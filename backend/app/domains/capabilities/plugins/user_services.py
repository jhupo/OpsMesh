"""Plugin delegation to resource discovery and existing knowledge/memory services."""

from opsmesh_plugin_sdk.services.knowledge import (
    KnowledgeHit,
    KnowledgeQuery,
    MemoryReceipt,
    MemoryWrite,
)
from opsmesh_plugin_sdk.services.resources import ResourcePage, ResourceQuery, ResourceSummary
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.base import Base
from backend.app.core.errors import DomainError
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resource_queries import (
    ResourceQueryScope,
    bind_resource_queries,
    execution_resource_queries,
    unbind_resource_queries,
)
from backend.app.domains.access.resources import (
    RESOURCE_TABLES,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.agents.memory.semantic import (
    AgentSemanticMemoryService,
    SemanticMemoryConflictError,
    SemanticMemoryUpsert,
)
from backend.app.domains.capabilities.plugins.services import PluginPrincipal, PluginServices
from backend.app.domains.capabilities.tools.workspace_memory import WorkspaceMemorySearchService
from backend.app.observability.audit.service import AuditService


class PluginUserServices:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.services = PluginServices(session)

    def resources(self, principal: PluginPrincipal, query: ResourceQuery) -> ResourcePage:
        user = self.services.resolve_user(principal, query, "resources.read")
        kind = ResourceKind(query.resource_kind)
        table = Base.metadata.tables[RESOURCE_TABLES[kind]]
        authorization = ResourceAuthorizationService(self.session, user)
        # Only public labels are selected: never ORM serialization of credentials or locators.
        labels = {
            "mcp_tool": "tool_name",
            "skill": "installed_name",
            "task": "title",
            "session": "session_key",
            "thread": "subject",
            "file": "filename",
            "memory": "title",
        }
        label = (
            table.c.configuration["name"].as_string()
            if kind == ResourceKind.AUTOMATION
            else table.c[labels.get(kind.value, "name")]
        )
        rows = self.session.execute(
            select(table.c.id, label.label("name"))
            .where(
                table.c.workspace_id == principal.workspace_id,
                authorization.predicate(principal.workspace_id, kind, table.c.id),
                authorization.predicate(
                    principal.workspace_id, kind, table.c.id, ResourceAction(query.action)
                ),
            )
            .order_by(table.c.id)
            .offset(query.offset)
            .limit(query.limit + 1)
        ).all()
        return ResourcePage(
            items=[
                ResourceSummary(
                    id=row.id,
                    kind=query.resource_kind,
                    name=row.name,
                    actions=[
                        action.value
                        for action in authorization.effective_actions(
                            principal.workspace_id, kind, row.id
                        )
                    ],
                )
                for row in rows[: query.limit]
            ],
            has_more=len(rows) > query.limit,
        )

    def search(self, principal: PluginPrincipal, query: KnowledgeQuery) -> list[KnowledgeHit]:
        user = self.services.resolve_user(principal, query, "knowledge.read")
        # Retrieval updates access counters as trusted service evidence; all candidate reads
        # remain constrained to the restored user's resource visibility.
        with execution_resource_queries(self.session, principal.workspace_id, user):
            results = WorkspaceMemorySearchService(self.session).search(
                workspace_id=principal.workspace_id,
                query=query.query,
                limit=query.limit,
                memory_layers={"semantic"},
            )
            output = []
            for item in results:
                metadata = item.get("metadata")
                entry_id = metadata.get("memory_entry_id") if isinstance(metadata, dict) else None
                output.append(
                    KnowledgeHit.model_validate(
                        {
                            "id": entry_id,
                            **{
                                key: item[key]
                                for key in ("title", "snippet", "source_type", "source_id", "score")
                            },
                        }
                    )
                )
        return output

    def remember(self, principal: PluginPrincipal, request: MemoryWrite) -> MemoryReceipt:
        user = self.services.resolve_user(principal, request, "memory.write")
        AuthorizationService(self.session).require_workspace(
            user_id=user.user_id,
            workspace_id=principal.workspace_id,
            action=WorkspaceAction.WRITE,
            authenticated_user=user,
        )
        if request.scope_type != "workspace":
            ResourceAuthorizationService(self.session, user).require(
                principal.workspace_id,
                ResourceKind(request.scope_type),
                request.scope_id,
                ResourceAction.UPDATE,
            )
        bind_resource_queries(self.session, ResourceQueryScope(principal.workspace_id, user))
        try:
            entry = AgentSemanticMemoryService(self.session).upsert(
                SemanticMemoryUpsert(
                    workspace_id=principal.workspace_id,
                    scope_type=request.scope_type,
                    scope_id=request.scope_id,
                    memory_key=request.memory_key,
                    knowledge_type=request.knowledge_type,
                    title=request.title,
                    content=request.content,
                    tags=request.tags,
                    importance=request.importance,
                    metadata={},
                    expected_revision=request.expected_revision,
                    changed_by_user_id=user.user_id,
                    change_reason="plugin.memory.write",
                )
            )
            AuditService(self.session).record_user_action(
                workspace_id=principal.workspace_id,
                user_id=user.user_id,
                action="plugin.memory.written",
                target_type="workspace_memory_entry",
                target_id=entry.id,
                metadata={"install_id": str(principal.install_id), "revision": entry.revision},
            )
            self.session.flush()
            return MemoryReceipt.model_validate(entry)
        except SemanticMemoryConflictError as exc:
            raise DomainError(
                "Memory revision conflict", code="memory_conflict", status_code=409
            ) from exc
        finally:
            unbind_resource_queries(self.session)
