from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.memory.authorization import AuthorizedMemoryScope
from backend.app.memory.configuration import WorkspaceMemoryConfigurationService
from backend.app.memory.models import WorkspaceMemoryEntry, WorkspaceMemoryRetrievalEvent
from backend.app.memory.policy import (
    default_lifecycle_policy,
    default_retrieval_policy,
    hybrid_retrieval_policy,
    memory_lifecycle_policy,
)
from backend.app.memory.search import (
    HybridMemorySearchBackend,
    LexicalMemorySearchBackend,
    MemorySearchBackend,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySearchRequest,
    PostgresFullTextMemorySearchBackend,
    PostgresVectorMemorySearchBackend,
    memory_document_allowed,
    query_fingerprint,
    query_term_fingerprints,
)
from backend.app.tools.workspace_memory_documents import (
    WorkspaceMemoryDocumentRepository,
    result_payload,
)


class WorkspaceMemorySearchService:
    """Workspace-scoped search across operational memory with pluggable backends."""

    def __init__(self, session: Session, ranker: MemorySearchBackend | None = None) -> None:
        self._session = session
        self._backend = ranker
        self._documents = WorkspaceMemoryDocumentRepository(session)

    def search(
        self,
        *,
        workspace_id: UUID,
        query: str,
        limit: int = 10,
        source_types: set[str] | None = None,
        memory_layers: set[str] | None = None,
        access_scopes: tuple[AuthorizedMemoryScope, ...] | None = None,
        layer_limits: dict[str, int] | None = None,
        query_embedding: list[float] | None = None,
        embedding_model: str | None = None,
        query_embedding_evidence: dict[str, object] | None = None,
        agent_run_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []

        request = MemorySearchRequest(
            workspace_id=workspace_id,
            query=query,
            limit=limit,
            source_types=source_types,
            documents=self._candidate_documents(
                workspace_id,
                source_types,
                memory_layers,
                access_scopes,
            ),
            memory_layers=memory_layers,
            access_scopes=access_scopes,
            layer_limits=dict(layer_limits or {}),
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            query_embedding_evidence=query_embedding_evidence or {},
        )
        backend = self._backend or self._default_backend(request.workspace_id)
        hits = backend.search(request)
        if not hits and backend.backend_name not in {
            LexicalMemorySearchBackend.backend_name,
            HybridMemorySearchBackend.backend_name,
        }:
            hits = LexicalMemorySearchBackend().search(request)
        selected_hits = _select_hits(hits, limit=limit, layer_limits=request.layer_limits)
        self._record_accesses(workspace_id, selected_hits)
        self._record_retrieval(
            request,
            selected_hits,
            agent_run_id=agent_run_id,
            backend_name=backend.backend_name,
        )
        return [result_payload(hit) for hit in selected_hits]

    def _candidate_documents(
        self,
        workspace_id: UUID,
        source_types: set[str] | None,
        memory_layers: set[str] | None,
        access_scopes: tuple[AuthorizedMemoryScope, ...] | None,
    ) -> list[MemorySearchDocument]:
        indexed_sources = self._documents.indexed_sources(workspace_id)
        candidates: list[MemorySearchDocument] = []
        for candidate in self._documents.candidates(workspace_id):
            if source_types is not None and candidate.source_type not in source_types:
                continue
            if not memory_document_allowed(
                candidate,
                workspace_id=workspace_id,
                source_types=source_types,
                memory_layers=memory_layers,
                access_scopes=access_scopes,
            ):
                continue
            if (
                candidate.source_type,
                str(candidate.source_id),
            ) in indexed_sources and not candidate.metadata.get("indexed"):
                continue
            candidates.append(candidate)
        return candidates

    def _default_backend(self, workspace_id: UUID) -> MemorySearchBackend:
        if self._session.get_bind().dialect.name != "postgresql":
            return LexicalMemorySearchBackend()
        configuration = WorkspaceMemoryConfigurationService(self._session).get(workspace_id)
        retrieval_policy = hybrid_retrieval_policy(
            configuration.retrieval_policy
            if configuration is not None
            else default_retrieval_policy()
        )
        lifecycle_policy = memory_lifecycle_policy(
            configuration.lifecycle_policy
            if configuration is not None
            else default_lifecycle_policy()
        )
        return HybridMemorySearchBackend(
            full_text=PostgresFullTextMemorySearchBackend(self._session),
            vector=PostgresVectorMemorySearchBackend(self._session),
            lexical=LexicalMemorySearchBackend(),
            retrieval_policy=retrieval_policy,
            lifecycle_policy=lifecycle_policy,
        )

    def _record_accesses(
        self,
        workspace_id: UUID,
        hits: list[MemorySearchHit],
    ) -> None:
        memory_entry_ids = {
            parsed
            for hit in hits
            if (parsed := _memory_entry_id(hit)) is not None
        }
        if not memory_entry_ids:
            return
        entries = self._session.scalars(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.id.in_(memory_entry_ids),
                WorkspaceMemoryEntry.status == "active",
            )
        ).all()
        now = datetime.now(UTC)
        for entry in entries:
            entry.access_count += 1
            entry.last_accessed_at = now
        self._session.flush(entries)

    def _record_retrieval(
        self,
        request: MemorySearchRequest,
        hits: list[MemorySearchHit],
        *,
        agent_run_id: UUID | None,
        backend_name: str,
    ) -> None:
        ranking_policy: dict[str, object] = {"backend": backend_name}
        configuration = WorkspaceMemoryConfigurationService(self._session).get(
            request.workspace_id
        )
        if configuration is not None:
            ranking_policy = {
                "backend": backend_name,
                "configuration_version": configuration.version,
                **configuration.retrieval_policy,
            }
        backend_counts: dict[str, int] = {}
        selected: list[dict[str, object]] = []
        candidate_count = 0
        deduplicated_count = 0
        for rank, hit in enumerate(hits, start=1):
            backend_ranks = hit.ranking_details.get("backend_ranks")
            if isinstance(backend_ranks, dict):
                for ranked_backend_name in backend_ranks:
                    name = str(ranked_backend_name)
                    backend_counts[name] = backend_counts.get(name, 0) + 1
            else:
                backend_counts[hit.backend_name] = backend_counts.get(hit.backend_name, 0) + 1
            raw_candidates = hit.ranking_details.get("candidate_count")
            if isinstance(raw_candidates, int):
                candidate_count = max(candidate_count, raw_candidates)
            raw_deduplicated = hit.ranking_details.get("deduplicated_count")
            if isinstance(raw_deduplicated, int):
                deduplicated_count = max(deduplicated_count, raw_deduplicated)
            selected.append(
                {
                    "rank": rank,
                    "memory_entry_id": hit.document.metadata.get("memory_entry_id"),
                    "source_type": hit.document.source_type,
                    "source_id": str(hit.document.source_id),
                    "scope_type": hit.document.metadata.get("scope_type"),
                    "scope_id": hit.document.metadata.get("scope_id"),
                    "score": hit.score,
                    "ranking": hit.ranking_details,
                }
            )
        self._session.add(
            WorkspaceMemoryRetrievalEvent(
                workspace_id=request.workspace_id,
                agent_run_id=agent_run_id,
                query_fingerprint=query_fingerprint(request.query),
                query_terms=query_term_fingerprints(request.query)[:64],
                requested_limit=request.limit,
                scope_filters={
                    "source_types": sorted(request.source_types)
                    if request.source_types is not None
                    else None,
                    "memory_layers": sorted(request.memory_layers)
                    if request.memory_layers is not None
                    else None,
                    "access_scopes": [scope.evidence() for scope in request.access_scopes]
                    if request.access_scopes is not None
                    else None,
                    "layer_limits": request.layer_limits,
                },
                ranking_policy=ranking_policy,
                backend_evidence=[
                    {"backend": name, "selected_hits": count}
                    for name, count in sorted(backend_counts.items())
                ]
                + (
                    [{"backend": "query_embedding", **request.query_embedding_evidence}]
                    if request.query_embedding_evidence
                    else []
                ),
                selected=selected,
                candidate_count=max(candidate_count, len(hits)),
                deduplicated_count=deduplicated_count,
            )
        )
        self._session.flush()


def _memory_entry_id(hit: MemorySearchHit) -> UUID | None:
    raw = hit.document.metadata.get("memory_entry_id")
    try:
        return UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _select_hits(
    hits: list[MemorySearchHit],
    *,
    limit: int,
    layer_limits: dict[str, int],
) -> list[MemorySearchHit]:
    if not layer_limits:
        return hits[:limit]
    selected: list[MemorySearchHit] = []
    counts: dict[str, int] = {}
    for hit in hits:
        raw_layer = hit.document.metadata.get("memory_layer")
        layer = raw_layer if isinstance(raw_layer, str) else "semantic"
        layer_limit = layer_limits.get(layer)
        if layer_limit is not None and counts.get(layer, 0) >= layer_limit:
            continue
        selected.append(hit)
        counts[layer] = counts.get(layer, 0) + 1
        if len(selected) >= limit:
            break
    return selected
