from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, false, func, literal_column, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.app.memory.authorization import AuthorizedMemoryScope
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.memory.policy import HybridMemoryRetrievalPolicy, MemoryLifecyclePolicy

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
    ranking_details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemorySearchRequest:
    workspace_id: UUID
    query: str
    limit: int
    source_types: set[str] | None
    documents: list[MemorySearchDocument]
    memory_layers: set[str] | None = None
    access_scopes: tuple[AuthorizedMemoryScope, ...] | None = None
    layer_limits: dict[str, int] = field(default_factory=dict)
    query_embedding: list[float] | None = None
    embedding_model: str | None = None
    query_embedding_evidence: dict[str, object] = field(default_factory=dict)


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
            if not _document_matches_request(document, request):
                continue
            score = lexical_score(document, terms, request.query)
            if score > 0:
                results.append((score, document))
        results.sort(
            key=lambda item: (
                item[0],
                created_at_sort_key(item[1].created_at),
                item[1].title.lower(),
                str(item[1].source_id),
            ),
            reverse=True,
        )
        return [
            MemorySearchHit(
                document=document,
                score=float(score),
                snippet=snippet(document.text, terms),
                backend_name=self.backend_name,
                ranking_details={"lexical_score": score},
            )
            for score, document in results[: _backend_result_limit(request)]
        ]


class PostgresFullTextMemorySearchBackend:
    backend_name = "postgres_full_text"

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        bind = self._session.get_bind()
        if (
            bind.dialect.name != "postgresql"
            or request.limit <= 0
            or not query_terms(request.query)
        ):
            return []
        query = func.plainto_tsquery("simple", request.query)
        vector = func.to_tsvector(
            "simple",
            WorkspaceMemoryEntry.title + literal_column("' '") + WorkspaceMemoryEntry.content,
        )
        statement = (
            select(WorkspaceMemoryEntry, func.ts_rank_cd(vector, query).label("score"))
            .where(*_memory_entry_filters(request), vector.op("@@")(query))
            .order_by(
                func.ts_rank_cd(vector, query).desc(),
                WorkspaceMemoryEntry.updated_at.desc(),
                WorkspaceMemoryEntry.id.asc(),
            )
            .limit(_backend_result_limit(request))
        )
        return [
            MemorySearchHit(
                document=_entry_document(entry),
                score=float(score or 0),
                snippet=snippet(entry.content, query_terms(request.query)),
                backend_name=self.backend_name,
                ranking_details={"full_text_score": float(score or 0)},
            )
            for entry, score in self._session.execute(statement).all()
        ]


class PostgresVectorMemorySearchBackend:
    backend_name = "postgres_vector"

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        bind = self._session.get_bind()
        if (
            bind.dialect.name != "postgresql"
            or request.limit <= 0
            or request.query_embedding is None
            or request.embedding_model is None
        ):
            return []
        distance = WorkspaceMemoryEntry.embedding.cosine_distance(request.query_embedding)
        statement = (
            select(WorkspaceMemoryEntry, distance.label("distance"))
            .where(
                *_memory_entry_filters(request),
                WorkspaceMemoryEntry.embedding_status == "ready",
                WorkspaceMemoryEntry.embedding_model == request.embedding_model,
                WorkspaceMemoryEntry.embedding_content_fingerprint
                == WorkspaceMemoryEntry.content_fingerprint,
                WorkspaceMemoryEntry.embedding.is_not(None),
            )
            .order_by(distance.asc(), WorkspaceMemoryEntry.id.asc())
            .limit(_backend_result_limit(request))
        )
        terms = query_terms(request.query)
        hits = []
        for entry, raw_distance in self._session.execute(statement).all():
            cosine_distance = max(float(raw_distance or 0), 0)
            hits.append(
                MemorySearchHit(
                    document=_entry_document(entry),
                    score=max(0.0, 1.0 - cosine_distance),
                    snippet=snippet(entry.content, terms),
                    backend_name=self.backend_name,
                    ranking_details={"cosine_distance": round(cosine_distance, 8)},
                )
            )
        return hits


class HybridMemorySearchBackend:
    backend_name = "hybrid_rrf"

    def __init__(
        self,
        *,
        full_text: MemorySearchBackend,
        vector: MemorySearchBackend,
        lexical: MemorySearchBackend,
        retrieval_policy: HybridMemoryRetrievalPolicy,
        lifecycle_policy: MemoryLifecyclePolicy,
        now: datetime | None = None,
    ) -> None:
        self._backends = (
            (full_text, retrieval_policy.full_text_weight),
            (vector, retrieval_policy.vector_weight),
            (lexical, retrieval_policy.lexical_weight),
        )
        self._retrieval_policy = retrieval_policy
        self._lifecycle_policy = lifecycle_policy
        self._now = _as_utc(now or datetime.now(UTC))

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        if request.limit <= 0:
            return []
        expanded = replace(
            request,
            limit=min(request.limit * self._retrieval_policy.candidate_multiplier, 200),
        )
        candidates: dict[str, _FusedCandidate] = {}
        raw_count = 0
        for backend, weight in self._backends:
            backend_hits = backend.search(expanded)
            raw_count += len(backend_hits)
            for rank, hit in enumerate(backend_hits, start=1):
                identity = _deduplication_identity(hit.document)
                candidate = candidates.get(identity)
                contribution = weight / (
                    self._retrieval_policy.reciprocal_rank_constant + rank
                )
                if candidate is None:
                    candidate = _FusedCandidate(hit=hit)
                    candidates[identity] = candidate
                elif _prefer_hit(hit, candidate.hit):
                    candidate.hit = hit
                candidate.rrf_score += contribution
                candidate.backend_ranks[backend.backend_name] = rank
                candidate.backend_scores[backend.backend_name] = round(hit.score, 8)
                candidate.backend_contributions[backend.backend_name] = round(contribution, 8)
        ranked: list[MemorySearchHit] = []
        deduplicated_count = max(raw_count - len(candidates), 0)
        for identity, candidate in candidates.items():
            importance_score = _effective_importance_score(
                candidate.hit.document,
                self._lifecycle_policy,
                self._now,
            )
            recency_score = _recency_score(
                candidate.hit.document.created_at,
                half_life_days=self._retrieval_policy.recency_half_life_days,
                now=self._now,
            )
            final_score = (
                candidate.rrf_score
                + self._retrieval_policy.importance_weight * importance_score
                + self._retrieval_policy.recency_weight * recency_score
            )
            ranked.append(
                MemorySearchHit(
                    document=candidate.hit.document,
                    score=round(final_score, 8),
                    snippet=candidate.hit.snippet,
                    backend_name=self.backend_name,
                    ranking_details={
                        "deduplication_key": identity,
                        "backend_ranks": candidate.backend_ranks,
                        "backend_scores": candidate.backend_scores,
                        "backend_contributions": candidate.backend_contributions,
                        "rrf_score": round(candidate.rrf_score, 8),
                        "effective_importance": round(importance_score, 8),
                        "recency_score": round(recency_score, 8),
                        "candidate_count": raw_count,
                        "deduplicated_count": deduplicated_count,
                    },
                )
            )
        ranked.sort(
            key=lambda hit: (
                hit.score,
                created_at_sort_key(hit.document.created_at),
                hit.document.title.lower(),
                str(hit.document.source_id),
            ),
            reverse=True,
        )
        return ranked[: _backend_result_limit(request)]


@dataclass
class _FusedCandidate:
    hit: MemorySearchHit
    rrf_score: float = 0.0
    backend_ranks: dict[str, int] = field(default_factory=dict)
    backend_scores: dict[str, float] = field(default_factory=dict)
    backend_contributions: dict[str, float] = field(default_factory=dict)


def query_fingerprint(query: str) -> str:
    normalized = " ".join(query.split()).strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def query_term_fingerprints(query: str) -> list[str]:
    return [f"sha256:{hashlib.sha256(term.encode()).hexdigest()}" for term in query_terms(query)]


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
    return _as_utc(value).timestamp()


def _memory_entry_filters(request: MemorySearchRequest) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        WorkspaceMemoryEntry.workspace_id == request.workspace_id,
        WorkspaceMemoryEntry.status == "active",
        WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
        or_(
            WorkspaceMemoryEntry.expires_at.is_(None),
            WorkspaceMemoryEntry.expires_at > datetime.now(UTC),
        ),
    ]
    if request.memory_layers is not None:
        filters.append(WorkspaceMemoryEntry.memory_layer.in_(request.memory_layers))
    if request.source_types is not None:
        filters.append(_source_type_condition(request.source_types))
    if request.access_scopes is not None:
        filters.append(
            or_(*(_access_scope_condition(scope) for scope in request.access_scopes))
            if request.access_scopes
            else false()
        )
    return filters


def _access_scope_condition(scope: AuthorizedMemoryScope) -> ColumnElement[bool]:
    conditions: list[ColumnElement[bool]] = []
    if scope.source_types is not None:
        conditions.append(_source_type_condition(set(scope.source_types)))
    if scope.tags is not None:
        conditions.append(WorkspaceMemoryEntry.tags.op("?|")(sorted(scope.tags)))
    if scope.scope_types is not None:
        conditions.append(WorkspaceMemoryEntry.scope_type.in_(scope.scope_types))
    if scope.scope_ids is not None:
        conditions.append(WorkspaceMemoryEntry.scope_id.in_(scope.scope_ids))
    return and_(*conditions) if conditions else true()


def _source_type_condition(source_types: set[str]) -> ColumnElement[bool]:
    conditions: list[ColumnElement[bool]] = []
    if "workspace_memory" in source_types:
        conditions.append(WorkspaceMemoryEntry.entry_type != "indexed_chunk")
    indexed_types = source_types - {"workspace_memory"}
    if indexed_types:
        conditions.append(
            and_(
                WorkspaceMemoryEntry.entry_type == "indexed_chunk",
                WorkspaceMemoryEntry.source_type.in_(indexed_types),
            )
        )
    return or_(*conditions) if conditions else false()


def _document_matches_request(
    document: MemorySearchDocument,
    request: MemorySearchRequest,
) -> bool:
    return memory_document_allowed(
        document,
        workspace_id=request.workspace_id,
        source_types=request.source_types,
        memory_layers=request.memory_layers,
        access_scopes=request.access_scopes,
    )


def memory_document_allowed(
    document: MemorySearchDocument,
    *,
    workspace_id: UUID,
    source_types: set[str] | None,
    memory_layers: set[str] | None,
    access_scopes: tuple[AuthorizedMemoryScope, ...] | None,
) -> bool:
    if source_types is not None and document.source_type not in source_types:
        return False
    memory_layer = document.metadata.get("memory_layer")
    normalized_layer = memory_layer if isinstance(memory_layer, str) else "semantic"
    if memory_layers is not None and normalized_layer not in memory_layers:
        return False
    if access_scopes is None:
        return True
    tags = document.metadata.get("tags")
    normalized_tags = {
        item for item in tags if isinstance(item, str)
    } if isinstance(tags, list) else set()
    scope_type = document.metadata.get("scope_type")
    normalized_scope_type = scope_type if isinstance(scope_type, str) else "workspace"
    scope_id = document.metadata.get("scope_id")
    normalized_scope_id = (
        scope_id if isinstance(scope_id, str) else str(workspace_id)
    )
    return any(
        scope.allows(
            source_type=document.source_type,
            tags=normalized_tags,
            scope_type=normalized_scope_type,
            scope_id=normalized_scope_id,
        )
        for scope in access_scopes
    )


def _backend_result_limit(request: MemorySearchRequest) -> int:
    configured = sum(max(value, 0) for value in request.layer_limits.values())
    return min(max(request.limit, configured), 200)


def _entry_document(entry: WorkspaceMemoryEntry) -> MemorySearchDocument:
    return MemorySearchDocument(
        source_type=_entry_source_type(entry),
        source_id=_entry_source_id(entry),
        title=entry.title,
        text=entry.content,
        created_at=entry.created_at,
        metadata=memory_entry_document_metadata(entry),
    )


def memory_entry_document_metadata(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    source_metadata = dict(entry.memory_metadata)
    metadata: dict[str, object] = {
        "memory_entry_id": str(entry.id),
        "content_fingerprint": entry.content_fingerprint,
        "entry_type": entry.entry_type,
        "memory_layer": entry.memory_layer,
        "scope_type": entry.scope_type,
        "scope_id": entry.scope_id,
        "tags": list(entry.tags),
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "access_count": entry.access_count,
        "last_accessed_at": _dt_or_none(entry.last_accessed_at),
        "updated_at": _dt_or_none(entry.updated_at),
        "source_type": entry.source_type,
        "source_id": entry.source_id,
        "source_metadata": source_metadata,
    }
    if entry.entry_type == "indexed_chunk":
        metadata.update(
            {
                "indexed": True,
                "chunk_index": source_metadata.get("chunk_index"),
                "chunk_count": source_metadata.get("chunk_count"),
            }
        )
    return metadata


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


def _deduplication_identity(document: MemorySearchDocument) -> str:
    fingerprint = document.metadata.get("content_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        return f"sha256:{fingerprint}"
    return f"source:{document.source_type}:{document.source_id}"


def _prefer_hit(candidate: MemorySearchHit, current: MemorySearchHit) -> bool:
    return (
        candidate.score,
        created_at_sort_key(candidate.document.created_at),
        candidate.document.title.lower(),
    ) > (
        current.score,
        created_at_sort_key(current.document.created_at),
        current.document.title.lower(),
    )


def _effective_importance_score(
    document: MemorySearchDocument,
    policy: MemoryLifecyclePolicy,
    now: datetime,
) -> float:
    raw_importance = document.metadata.get("importance")
    importance = float(raw_importance) if isinstance(raw_importance, int | float) else 0.0
    memory_layer = document.metadata.get("memory_layer")
    half_life = (
        policy.episodic_decay_half_life_days
        if memory_layer == "episodic"
        else policy.semantic_decay_half_life_days
    )
    reference = _datetime_or_none(document.metadata.get("last_accessed_at"))
    if reference is None:
        reference = _datetime_or_none(document.metadata.get("updated_at")) or document.created_at
    decay = _recency_score(reference, half_life_days=half_life, now=now)
    return max(0.0, min(importance / 100.0, 1.0)) * decay


def _recency_score(
    value: datetime | None,
    *,
    half_life_days: int,
    now: datetime,
) -> float:
    if value is None:
        return 0.0
    age_days = max((now - _as_utc(value)).total_seconds() / 86_400, 0)
    return math.pow(0.5, age_days / half_life_days)


def _datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
