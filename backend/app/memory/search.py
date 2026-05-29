from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.memory.models import WorkspaceMemoryEntry

TOKEN_PATTERN = re.compile(r"[\w.-]+", re.UNICODE)
SNIPPET_LENGTH = 220


@dataclass(frozen=True)
class MemorySearchDocument:
    source_type: str
    source_id: UUID
    title: str
    text: str
    created_at: datetime | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class MemorySearchHit:
    document: MemorySearchDocument
    score: float
    snippet: str
    backend_name: str


@dataclass(frozen=True)
class MemorySearchRequest:
    workspace_id: UUID
    query: str
    limit: int
    source_types: set[str] | None
    documents: list[MemorySearchDocument]


class MemorySearchBackend(Protocol):
    backend_name: str

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]: ...


class LexicalMemorySearchBackend:
    backend_name = "lexical"

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        terms = query_terms(request.query)
        if not terms or request.limit <= 0:
            return []
        results: list[tuple[int, MemorySearchDocument]] = []
        for document in request.documents:
            if (
                request.source_types is not None
                and document.source_type not in request.source_types
            ):
                continue
            score = lexical_score(document, terms, request.query)
            if score > 0:
                results.append((score, document))
        results.sort(
            key=lambda item: (
                item[0],
                created_at_sort_key(item[1].created_at),
                item[1].title.lower(),
            ),
            reverse=True,
        )
        return [
            MemorySearchHit(
                document=document,
                score=float(score),
                snippet=snippet(document.text, terms),
                backend_name=self.backend_name,
            )
            for score, document in results[: request.limit]
        ]


class PostgresFullTextMemorySearchBackend:
    backend_name = "postgres_full_text"

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        bind = self._session.get_bind()
        if bind.dialect.name != "postgresql" or request.limit <= 0:
            return []
        query = func.plainto_tsquery("simple", request.query)
        vector = func.to_tsvector(
            "simple",
            func.concat(WorkspaceMemoryEntry.title, " ", WorkspaceMemoryEntry.content),
        )
        statement = (
            select(WorkspaceMemoryEntry, func.ts_rank_cd(vector, query).label("score"))
            .where(
                WorkspaceMemoryEntry.workspace_id == request.workspace_id,
                WorkspaceMemoryEntry.status == "active",
                vector.op("@@")(query),
            )
            .order_by(func.ts_rank_cd(vector, query).desc(), WorkspaceMemoryEntry.updated_at.desc())
            .limit(request.limit)
        )
        if request.source_types is not None:
            statement = statement.where(WorkspaceMemoryEntry.source_type.in_(request.source_types))
        hits: list[MemorySearchHit] = []
        terms = query_terms(request.query)
        for entry, score in self._session.execute(statement).all():
            document = MemorySearchDocument(
                source_type=_entry_source_type(entry),
                source_id=_entry_source_id(entry),
                title=entry.title,
                text=entry.content,
                created_at=entry.created_at,
                metadata={
                    "entry_type": entry.entry_type,
                    "tags": entry.tags,
                    "visibility_scope": entry.visibility_scope,
                    "importance": entry.importance,
                    "source_type": entry.source_type,
                    "source_id": entry.source_id,
                    **entry.memory_metadata,
                },
            )
            hits.append(
                MemorySearchHit(
                    document=document,
                    score=float(score or 0),
                    snippet=snippet(document.text, terms),
                    backend_name=self.backend_name,
                )
            )
        return hits


def query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in TOKEN_PATTERN.findall(query.lower()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def lexical_score(document: MemorySearchDocument, terms: list[str], query: str) -> int:
    title = document.title.lower()
    text = document.text.lower()
    phrase = " ".join(query_terms(query))
    score = 0
    if phrase and phrase in title:
        score += 30
    elif phrase and phrase in text:
        score += 16
    for term in terms:
        if term in title:
            score += 10
        if term in text:
            score += 3
    return score


def snippet(text: str, terms: list[str]) -> str:
    collapsed = " ".join(text.split())
    lower = collapsed.lower()
    positions = [lower.find(term) for term in terms if term in lower]
    start = max(0, min(positions) - 60) if positions else 0
    value = collapsed[start : start + SNIPPET_LENGTH]
    if start > 0:
        value = f"...{value}"
    if start + SNIPPET_LENGTH < len(collapsed):
        value = f"{value}..."
    return value


def created_at_sort_key(value: datetime | None) -> float:
    if value is None:
        return 0
    return value.timestamp()


def _entry_source_type(entry: WorkspaceMemoryEntry) -> str:
    if entry.entry_type == "indexed_chunk" and entry.source_type:
        return entry.source_type
    return "workspace_memory"


def _entry_source_id(entry: WorkspaceMemoryEntry) -> UUID:
    if entry.entry_type == "indexed_chunk" and entry.source_id:
        try:
            return UUID(entry.source_id)
        except ValueError:
            return entry.id
    return entry.id
