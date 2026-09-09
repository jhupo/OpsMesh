from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.memory_policy import semantic_memory_policy
from backend.app.memory.authorization import AuthorizedMemoryScope
from backend.app.memory.embeddings import WorkspaceMemoryQueryEmbeddingService
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.memory.semantic import AgentSemanticMemoryService, SemanticMemoryUpsert
from backend.app.memory.working import AgentWorkingMemoryService
from backend.app.runs.models import AgentRun
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.events import ProductToolEventRecorder
from backend.app.tools.product_tools.normalization import normalized_tags
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService


class WorkspaceMemoryProductTools(ProductToolEventRecorder):
    _session: Session
    _memory_embedding_secret_service: SecretEncryptionService | None

    def search_workspace_memory(
        self,
        context: ToolContext,
        query: str,
        *,
        limit: int = 10,
        source_types: set[str] | None = None,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> list[dict[str, object]]:
        context.require_tool("search_workspace_memory")
        self._append_tool_event(context, "tool.called", "search_workspace_memory")
        query_embedding, embedding_model, embedding_evidence = self._query_embedding(
            workspace_id=context.workspace_id,
            query=query,
        )
        results = WorkspaceMemorySearchService(self._session).search(
            workspace_id=context.workspace_id,
            query=query,
            limit=limit,
            source_types=source_types,
            access_scopes=access_scopes,
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            query_embedding_evidence=embedding_evidence,
            agent_run_id=context.agent_run_id,
        )
        self._append_tool_event(context, "tool.completed", "search_workspace_memory")
        return results

    def _query_embedding(
        self,
        *,
        workspace_id: UUID,
        query: str,
    ) -> tuple[list[float] | None, str | None, dict[str, object]]:
        result = WorkspaceMemoryQueryEmbeddingService(
            self._session,
            self._memory_embedding_secret_service,
        ).generate(workspace_id=workspace_id, query=query)
        return result.vector, result.model, result.evidence

    def upsert_semantic_memory(
        self,
        context: ToolContext,
        *,
        scope_type: str,
        scope_id: UUID,
        memory_key: str,
        knowledge_type: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        importance: int = 50,
        metadata: dict[str, object] | None = None,
        expected_revision: int | None = None,
        change_reason: str | None = None,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> WorkspaceMemoryEntry:
        context.require_tool("upsert_semantic_memory")
        self._append_tool_event(context, "tool.called", "upsert_semantic_memory")
        run = self._run_for_context(context)
        if run is None:
            raise ValueError("Semantic memory writes require an agent run")
        self._require_semantic_write_policy(run)
        normalized_entry_tags = normalized_tags(tags)
        _require_memory_write(
            scope_type=scope_type,
            scope_id=scope_id,
            tags=set(normalized_entry_tags),
            access_scopes=access_scopes,
        )
        entry = AgentSemanticMemoryService(self._session).upsert(
            SemanticMemoryUpsert(
                workspace_id=context.workspace_id,
                scope_type=scope_type,
                scope_id=scope_id,
                memory_key=memory_key,
                knowledge_type=knowledge_type,
                title=title,
                content=content,
                tags=normalized_entry_tags,
                importance=importance,
                metadata=metadata or {},
                expected_revision=expected_revision,
                changed_by_agent_profile_id=run.agent_profile_id,
                changed_by_agent_run_id=run.id,
                change_reason=change_reason,
            )
        )
        self._append_tool_event(context, "tool.completed", "upsert_semantic_memory")
        return entry

    def archive_semantic_memory(
        self,
        context: ToolContext,
        memory_entry_id: UUID,
        *,
        expected_revision: int,
        change_reason: str | None = None,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> WorkspaceMemoryEntry:
        context.require_tool("archive_semantic_memory")
        self._append_tool_event(context, "tool.called", "archive_semantic_memory")
        run = self._run_for_context(context)
        if run is None:
            raise ValueError("Semantic memory archives require an agent run")
        self._require_semantic_write_policy(run)
        entry = AgentSemanticMemoryService(self._session).get(
            workspace_id=context.workspace_id,
            memory_entry_id=memory_entry_id,
        )
        if entry is None:
            raise ToolResourceNotFoundError("Semantic memory entry not found")
        if not any(
            scope.allows_write(
                scope_type=entry.scope_type,
                scope_id=entry.scope_id,
                tags=set(entry.tags),
            )
            for scope in access_scopes
        ):
            raise ToolResourceNotFoundError("Memory entry is outside the authorized resource scope")
        AgentSemanticMemoryService(self._session).archive(
            entry,
            expected_revision=expected_revision,
            changed_by_agent_profile_id=run.agent_profile_id,
            changed_by_agent_run_id=run.id,
            change_reason=change_reason,
        )
        self._append_tool_event(context, "tool.completed", "archive_semantic_memory")
        return entry

    def promote_working_memory(
        self,
        context: ToolContext,
        working_memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("promote_working_memory")
        self._append_tool_event(context, "tool.called", "promote_working_memory")
        if context.agent_run_id is None:
            raise ValueError("Working memory promotion requires an agent run")
        entry = AgentWorkingMemoryService(self._session).promote(
            workspace_id=context.workspace_id,
            run_id=context.agent_run_id,
            memory_entry_id=working_memory_entry_id,
        )
        self._session.flush([entry])
        self._append_tool_event(context, "tool.completed", "promote_working_memory")
        return entry

    def _run_for_context(self, context: ToolContext) -> AgentRun | None:
        if context.agent_run_id is None:
            return None
        run = self._session.get(AgentRun, context.agent_run_id)
        if run is None or run.workspace_id != context.workspace_id:
            return None
        return run

    @staticmethod
    def _require_semantic_write_policy(run: AgentRun) -> None:
        snapshot = run.input.get("authorization_snapshot")
        memory_policy = snapshot.get("memory_policy") if isinstance(snapshot, dict) else {}
        if not semantic_memory_policy(memory_policy).write_enabled:
            raise ValueError("Semantic memory writes are disabled for this agent run")


def _require_memory_write(
    *,
    scope_type: str,
    scope_id: UUID,
    tags: set[str],
    access_scopes: tuple[AuthorizedMemoryScope, ...],
) -> None:
    if not any(
        scope.allows_write(
            scope_type=scope_type,
            scope_id=str(scope_id),
            tags=tags,
        )
        for scope in access_scopes
    ):
        raise ValueError("Memory write is outside the authorized resource scope")
