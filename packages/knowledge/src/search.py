"""Scoped pgvector search with explicit exact and bounded ANN modes."""

from __future__ import annotations

from sqlalchemy import select, text

from .errors import IndexUnavailable
from .models import ChunkRecord, DocumentRecord
from .schemas import SearchHit


def search_statement(base_id, vector, *, epoch, fingerprint, limit):
    """Filter scope, state, profile, and publication before limiting hits."""
    distance = ChunkRecord.embedding.cosine_distance(vector).label("distance")
    return (
        select(ChunkRecord, DocumentRecord.name, distance)
        .join(DocumentRecord, DocumentRecord.id == ChunkRecord.document_id)
        .where(
            DocumentRecord.knowledge_base_id == base_id,
            DocumentRecord.status == "ready",
            DocumentRecord.index_epoch == epoch,
            DocumentRecord.indexed_profile == fingerprint,
            ChunkRecord.index_key == DocumentRecord.index_key,
        )
        .order_by(distance)
        .limit(limit)
    )


async def search(
    knowledge_base, query: str, *, limit: int | None = None
) -> list[SearchHit]:
    """Encode outside SQL and recheck a compatible library before retrieval."""
    runtime = knowledge_base.runtime
    if limit is None:
        limit = min(5, runtime.settings.max_results)
    if not isinstance(limit, int) or not 1 <= limit <= (
        runtime.settings.max_results
    ):
        raise ValueError("Search result count is outside configured bounds")
    async with runtime.access():
        # Validate before encoding, then recheck under a library lock afterward.
        async with runtime.database.session() as session:
            base = await knowledge_base.fetch(session)
            if (
                base.index_status != "ready"
                or base.active_profile != runtime.profile
            ):
                raise IndexUnavailable(
                    "Knowledge index needs rebuilding or is unavailable"
                )
        vector = await runtime.encoder.query(query)
        async with runtime.database.transaction() as session:
            base = await session.scalar(
                knowledge_base.statement().with_for_update(read=True)
            )
            if (
                base is None
                or base.index_status != "ready"
                or base.active_profile != runtime.profile
            ):
                raise IndexUnavailable(
                    "Knowledge index changed while the query was encoded"
                )
            if runtime.settings.search_mode == "exact":
                await session.execute(text("SET LOCAL enable_indexscan = off"))
                await session.execute(text("SET LOCAL enable_bitmapscan = off"))
            else:
                await session.execute(
                    text("SET LOCAL hnsw.iterative_scan = 'strict_order'")
                )
                await session.execute(
                    text("SELECT set_config('hnsw.ef_search', :value, true)"),
                    {"value": str(runtime.settings.hnsw_ef_search)},
                )
                await session.execute(
                    text(
                        "SELECT set_config('hnsw.max_scan_tuples', "
                        ":value, true)"
                    ),
                    {"value": str(runtime.settings.hnsw_max_scan_tuples)},
                )
            rows = await session.execute(
                search_statement(
                    base.id,
                    vector,
                    epoch=base.index_epoch,
                    fingerprint=runtime.encoder.profile.fingerprint,
                    limit=limit,
                )
            )
            hits = []
            remaining = runtime.settings.max_result_chars
            for chunk, name, distance in rows:
                if remaining <= 0:
                    break
                excerpt = chunk.text[:remaining]
                hits.append(
                    SearchHit(
                        document_id=chunk.document_id,
                        chunk_id=chunk.id,
                        title=name,
                        text=excerpt,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.start_offset + len(excerpt),
                        distance=float(distance),
                        index_key=chunk.index_key,
                    )
                )
                remaining -= len(excerpt)
            return hits
