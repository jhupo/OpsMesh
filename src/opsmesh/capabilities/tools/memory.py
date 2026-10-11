from __future__ import annotations

from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from opsmesh.capabilities.tools.contracts import ToolContext, ToolResourceNotFoundError
from opsmesh.capabilities.tools.events import ProductToolEventRecorder
from opsmesh.capabilities.tools.normalization import normalized_tags
from opsmesh.capabilities.tools.workspace_memory import WorkspaceMemorySearchService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.resources.knowledge.ingestion import KnowledgeSourceIngestionService
from opsmesh.resources.knowledge.models import KnowledgeCitation
from opsmesh.resources.memory.authorization import AuthorizedMemoryScope
from opsmesh.resources.memory.embedding_service import WorkspaceMemoryQueryEmbeddingService
from opsmesh.resources.memory.models import WorkspaceMemoryEntry, WorkspaceMemoryVersion
from opsmesh.resources.memory.policy import semantic_memory_policy
from opsmesh.resources.memory.retrieval_search import memory_scope_predicate
from opsmesh.resources.memory.semantic import AgentSemanticMemoryService, SemanticMemoryUpsert
from opsmesh.resources.memory.working import AgentWorkingMemoryService
from opsmesh.shared.db.pagination import page_scalars_by_offset
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import string_list


class KnowledgeCitationAccessError(ToolResourceNotFoundError):
    code = "knowledge_citation_access_denied"


class WorkspaceMemoryProductTools:
    def __init__(
        self,
        session: Session,
        events: ProductToolEventRecorder,
        *,
        memory_embedding_secret_service: SecretEncryptionService | None,
    ) -> None:
        self._session = session
        self._events = events
        self._memory_embedding_secret_service = memory_embedding_secret_service

    def list_semantic_memory(
        self,
        context: ToolContext,
        *,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> dict[str, object]:
        context.require_tool("list_semantic_memory")
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("Invalid memory page")
        statement = select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == context.workspace_id,
            WorkspaceMemoryEntry.source_type == "semantic_memory",
            WorkspaceMemoryEntry.memory_layer == "semantic",
            or_(*(memory_scope_predicate(scope) for scope in access_scopes))
            if access_scopes
            else false(),
        )
        if not include_archived:
            statement = statement.where(WorkspaceMemoryEntry.status == "active")
        entries, total = page_scalars_by_offset(
            self._session,
            statement.order_by(WorkspaceMemoryEntry.updated_at.desc(), WorkspaceMemoryEntry.id),
            limit=limit,
            offset=offset,
        )
        self._events.append(context, "tool.completed", "list_semantic_memory")
        return {"items": [_semantic_payload(entry) for entry in entries], "total": total}

    def get_semantic_memory(
        self,
        context: ToolContext,
        *,
        memory_entry_id: UUID,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
        revision: int | None = None,
    ) -> dict[str, object]:
        context.require_tool("get_semantic_memory")
        entry = self._read_semantic_entry(context, memory_entry_id, access_scopes)
        result = _semantic_payload(entry) | {"content": entry.content}
        if revision is not None:
            version = self._session.scalar(
                select(WorkspaceMemoryVersion).where(
                    WorkspaceMemoryVersion.workspace_id == context.workspace_id,
                    WorkspaceMemoryVersion.memory_entry_id == entry.id,
                    WorkspaceMemoryVersion.revision == revision,
                )
            )
            if version is None or not any(
                scope.allows(
                    source_type="workspace_memory",
                    tags=set(string_list(version.snapshot.get("tags"))),
                    scope_type=str(version.snapshot.get("scope_type", "")),
                    scope_id=str(version.snapshot.get("scope_id", "")),
                )
                for scope in access_scopes
            ):
                raise ToolResourceNotFoundError("Memory revision not found in authorized scope")
            result = {"id": str(entry.id), "revision": version.revision, **version.snapshot}
        self._events.append(context, "tool.completed", "get_semantic_memory")
        return result

    def list_semantic_memory_revisions(
        self,
        context: ToolContext,
        *,
        memory_entry_id: UUID,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, object]:
        context.require_tool("list_semantic_memory_revisions")
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("Invalid memory history page")
        entry = self._read_semantic_entry(context, memory_entry_id, access_scopes)
        versions, total = page_scalars_by_offset(
            self._session,
            select(WorkspaceMemoryVersion)
            .where(
                WorkspaceMemoryVersion.workspace_id == context.workspace_id,
                WorkspaceMemoryVersion.memory_entry_id == entry.id,
            )
            .order_by(WorkspaceMemoryVersion.revision.desc()),
            limit=limit,
            offset=offset,
        )
        self._events.append(context, "tool.completed", "list_semantic_memory_revisions")
        return {
            "items": [
                {"revision": item.revision, "created_at": item.created_at.isoformat()}
                for item in versions
            ],
            "total": total,
        }

    def _read_semantic_entry(
        self,
        context: ToolContext,
        memory_entry_id: UUID,
        scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> WorkspaceMemoryEntry:
        entry = AgentSemanticMemoryService(self._session).get(
            workspace_id=context.workspace_id,
            memory_entry_id=memory_entry_id,
        )
        if entry is None or not any(
            scope.allows(
                source_type="workspace_memory",
                tags=set(entry.tags),
                scope_type=entry.scope_type,
                scope_id=entry.scope_id,
            )
            for scope in scopes
        ):
            raise ToolResourceNotFoundError("Memory entry not found in authorized scope")
        return entry

    def search_workspace_memory(
        self,
        context: ToolContext,
        query: str,
        *,
        limit: int = 5,
        source_types: set[str] | None = None,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
        tool_name: str = "search_workspace_memory",
    ) -> list[dict[str, object]]:
        if tool_name not in {"search_workspace_memory", "search_knowledge"}:
            raise ValueError("Unsupported memory search tool")
        context.require_tool(tool_name)
        if not 1 <= limit <= 10:
            raise ValueError("Memory search limit must be between 1 and 10")
        self._events.append(context, "tool.called", tool_name)
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
        memory_entry_ids = {
            parsed_id
            for result in results
            if result.get("source_type") == "knowledge_source"
            for metadata in [result.get("metadata")]
            if isinstance(metadata, dict)
            for raw_id in [metadata.get("memory_entry_id")]
            if raw_id is not None
            for parsed_id in [_parse_uuid(raw_id)]
            if parsed_id is not None
        }
        citations_by_entry = KnowledgeSourceIngestionService(
            self._session
        ).authorized_citations_for_memory_entries(
            workspace_id=context.workspace_id,
            memory_entry_ids=memory_entry_ids,
            access_scopes=access_scopes,
        )
        for result in results:
            if result.get("source_type") != "knowledge_source":
                continue
            metadata = result.get("metadata")
            memory_entry_id = (
                _parse_uuid(metadata.get("memory_entry_id")) if isinstance(metadata, dict) else None
            )
            if memory_entry_id is not None:
                result["citations"] = [
                    {"id": str(citation.id), "locator": citation.locator}
                    for citation in citations_by_entry.get(memory_entry_id, [])
                ]
        self._events.append(context, "tool.completed", tool_name)
        return [
            {
                "memory_entry_id": metadata.get("memory_entry_id"),
                "source_type": result["source_type"],
                "source_id": result["source_id"],
                "title": result["title"],
                "snippet": result["snippet"],
                "score": result["score"],
                "citations": result.get("citations", []),
            }
            for result in results
            for metadata in [result.get("metadata", {})]
            if isinstance(metadata, dict)
        ]

    def get_knowledge_citations(
        self,
        context: ToolContext,
        *,
        memory_entry_id: UUID,
        access_scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> dict[str, object]:
        context.require_tool("get_knowledge_citations")
        self._events.append(context, "tool.called", "get_knowledge_citations")
        try:
            citations = KnowledgeSourceIngestionService(
                self._session
            ).authorized_citations_for_memory_entry(
                workspace_id=context.workspace_id,
                memory_entry_id=memory_entry_id,
                access_scopes=access_scopes,
            )
        except ValueError as exc:
            raise KnowledgeCitationAccessError(
                "Knowledge citation is outside the authorized resource scope"
            ) from exc
        result = {
            "items": [_citation_payload(citation) for citation in citations],
            "total": len(citations),
        }
        self._events.append(context, "tool.completed", "get_knowledge_citations")
        return result

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
        self._events.append(context, "tool.called", "upsert_semantic_memory")
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
        self._events.append(context, "tool.completed", "upsert_semantic_memory")
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
        self._events.append(context, "tool.called", "archive_semantic_memory")
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
        self._events.append(context, "tool.completed", "archive_semantic_memory")
        return entry

    def promote_working_memory(
        self,
        context: ToolContext,
        working_memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("promote_working_memory")
        self._events.append(context, "tool.called", "promote_working_memory")
        if context.agent_run_id is None:
            raise ValueError("Working memory promotion requires an agent run")
        entry = AgentWorkingMemoryService(self._session).promote(
            workspace_id=context.workspace_id,
            run_id=context.agent_run_id,
            memory_entry_id=working_memory_entry_id,
        )
        self._session.flush([entry])
        self._events.append(context, "tool.completed", "promote_working_memory")
        return entry

    def _run_for_context(self, context: ToolContext) -> AgentRun | None:
        if context.agent_run_id is None:
            return None
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == context.workspace_id, AgentRun.id == context.agent_run_id
            )
        )
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


def _semantic_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": str(entry.id),
        "title": entry.title,
        "memory_key": entry.memory_key,
        "scope_type": entry.scope_type,
        "scope_id": entry.scope_id,
        "tags": list(entry.tags),
        "status": entry.status,
        "revision": entry.revision,
    }


def _citation_payload(citation: KnowledgeCitation) -> dict[str, object]:
    return {
        "id": str(citation.id),
        "source_id": str(citation.source_id),
        "memory_entry_id": str(citation.memory_entry_id),
        "source_version": citation.source_version,
        "chunk_index": citation.chunk_index,
        "locator": citation.locator,
        "start_offset": citation.start_offset,
        "end_offset": citation.end_offset,
        "quote": citation.quote,
    }


def _parse_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
