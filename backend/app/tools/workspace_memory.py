from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.memory.search import (
    LexicalMemorySearchBackend,
    MemorySearchBackend,
    MemorySearchDocument,
    MemorySearchRequest,
)
from backend.app.tools.workspace_memory_documents import (
    WorkspaceMemoryDocumentRepository,
    result_payload,
)


class WorkspaceMemorySearchService:
    """Workspace-scoped search across operational memory with pluggable backends."""

    def __init__(self, session: Session, ranker: MemorySearchBackend | None = None) -> None:
        self._backend = ranker or LexicalMemorySearchBackend()
        self._documents = WorkspaceMemoryDocumentRepository(session)

    def search(
        self,
        *,
        workspace_id: UUID,
        query: str,
        limit: int = 10,
        source_types: set[str] | None = None,
        tags: set[str] | None = None,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []

        request = MemorySearchRequest(
            workspace_id=workspace_id,
            query=query,
            limit=limit,
            source_types=source_types,
            documents=self._candidate_documents(workspace_id, source_types, tags),
            tags=tags,
        )
        hits = self._backend.search(request)
        if not hits and self._backend.backend_name != LexicalMemorySearchBackend.backend_name:
            hits = LexicalMemorySearchBackend().search(request)
        return [result_payload(hit) for hit in hits[:limit]]

    def _candidate_documents(
        self,
        workspace_id: UUID,
        source_types: set[str] | None,
        tags: set[str] | None,
    ) -> list[MemorySearchDocument]:
        indexed_sources = self._documents.indexed_sources(workspace_id)
        candidates: list[MemorySearchDocument] = []
        for candidate in self._documents.candidates(workspace_id):
            if source_types is not None and candidate.source_type not in source_types:
                continue
            candidate_tags = candidate.metadata.get("tags")
            if tags is not None and (
                not isinstance(candidate_tags, list)
                or not tags.intersection(item for item in candidate_tags if isinstance(item, str))
            ):
                continue
            if (
                candidate.source_type,
                str(candidate.source_id),
            ) in indexed_sources and not candidate.metadata.get("indexed"):
                continue
            candidates.append(candidate)
        return candidates
