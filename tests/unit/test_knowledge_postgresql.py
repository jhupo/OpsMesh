"""Optional real pgvector checks in a disposable schema per test."""

# ruff: noqa: E402

import importlib
import importlib.util
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

if importlib.util.find_spec("opsmesh.plugins.knowledge") is None:
    pytest.skip(
        "The knowledge plugin is not installed", allow_module_level=True
    )
pytest.importorskip("pgvector.sqlalchemy", exc_type=ImportError)
pytest.importorskip("agents", exc_type=ImportError)

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.plugins import registry
from opsmesh.plugins.model import Plugin

previous_driver = driver_state._driver
previous_plugins = registry._plugins
driver_state._driver = Driver(Env(), Config())
registry._plugins = previous_plugins.copy()
try:
    for dependency in ("database", "filesystem"):
        module = importlib.import_module("opsmesh.plugins." + dependency)
        registry._plugins.setdefault(
            dependency, Plugin(dependency, module, module.__name__, None)
        )
    pytest.importorskip("opsmesh.plugins.knowledge", exc_type=ImportError)
finally:
    driver_state._driver = previous_driver
    registry._plugins = previous_plugins

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from opsmesh.plugins.knowledge.config import Settings
from opsmesh.plugins.knowledge.indexing import IndexWorker
from opsmesh.plugins.knowledge.models import (
    ChunkRecord,
    DocumentRecord,
    KnowledgeBaseRecord,
    now,
)
from opsmesh.plugins.knowledge.schemas import IndexProfile, TextChunk
from opsmesh.plugins.knowledge.search import search_statement

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("OPSMESH_TEST_DSN"),
        reason="OPSMESH_TEST_DSN is not configured",
    ),
]


@pytest.fixture
async def database():
    dsn = os.environ["OPSMESH_TEST_DSN"]
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not dsn.startswith("postgresql+asyncpg://"):
        raise ValueError("OPSMESH_TEST_DSN must select PostgreSQL with asyncpg")
    schema = "knowledge_test_" + uuid4().hex
    engine = create_async_engine(
        dsn,
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            if (
                await connection.scalar(
                    text(
                        "SELECT extversion FROM pg_extension "
                        "WHERE extname = 'vector'"
                    )
                )
                is None
            ):
                pytest.skip(
                    "The test PostgreSQL server needs the vector extension"
                )
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

            def migrate(sync):
                migration = importlib.import_module(
                    "opsmesh.plugins.knowledge.migrations.0001_knowledge"
                )
                with Operations.context(MigrationContext.configure(sync)):
                    migration.upgrade()

            await connection.run_sync(migrate)

        @asynccontextmanager
        async def transaction():
            async with sessions() as session, session.begin():
                yield session

        yield SimpleNamespace(session=sessions, transaction=transaction)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        await engine.dispose()


def profile():
    return IndexProfile(
        revision=Settings().model_revision,
        manifest_digest="a" * 64,
        tokenizer_digest="b" * 64,
        chunk_tokens=400,
        overlap_tokens=64,
    ).persisted()


async def library(database, name="Selected"):
    async with database.transaction() as session:
        base = KnowledgeBaseRecord(
            owner_plugin="caller",
            name=name,
            active_profile=profile(),
            target_profile=profile(),
            index_status="ready",
        )
        session.add(base)
        await session.flush()
        return base


async def document(database, base, content="source", status="pending"):
    async with database.transaction() as session:
        record = DocumentRecord(
            knowledge_base_id=base.id,
            name="Document",
            media_type="text/plain",
            source_path=uuid4().hex,
            content_hash="a" * 64,
            text=content,
            index_epoch=base.index_epoch,
            status=status,
            indexed_profile=profile()["fingerprint"]
            if status == "ready"
            else None,
        )
        session.add(record)
        await session.flush()
        return record


async def test_real_scoped_vector_ordering_excludes_closer_foreign_library(
    database,
):
    selected, foreign = (
        await library(database),
        await library(database, "Foreign"),
    )
    own = await document(database, selected, status="ready")
    other = await document(database, foreign, status="ready")
    async with database.transaction() as session:
        session.add_all(
            [
                ChunkRecord(
                    document_id=own.id,
                    ordinal=0,
                    text="own",
                    start_offset=0,
                    end_offset=3,
                    token_count=1,
                    embedding=[0.0, 1.0] + [0.0] * 510,
                    index_key=own.index_key,
                ),
                ChunkRecord(
                    document_id=other.id,
                    ordinal=0,
                    text="foreign",
                    start_offset=0,
                    end_offset=7,
                    token_count=1,
                    embedding=[1.0] + [0.0] * 511,
                    index_key=other.index_key,
                ),
            ]
        )
    async with database.transaction() as session:
        await session.execute(text("SET LOCAL enable_indexscan = off"))
        rows = (
            await session.execute(
                search_statement(
                    selected.id,
                    [1.0] + [0.0] * 511,
                    epoch=selected.index_epoch,
                    fingerprint=profile()["fingerprint"],
                    limit=1,
                )
            )
        ).all()
    assert (
        len(rows) == 1
        and rows[0][0].document_id == own.id
        and rows[0][2] == 1.0
    )


async def test_real_skip_locked_claim_reclaim_and_stale_publication(database):
    base = await library(database)
    first = await document(database, base, "first")
    second = await document(database, base, "second")
    worker = IndexWorker(
        SimpleNamespace(
            database=database, settings=Settings(), profile=profile()
        )
    )
    async with database.transaction() as locker:
        await locker.scalar(
            select(DocumentRecord)
            .where(DocumentRecord.id == first.id)
            .with_for_update()
        )
        claim = await worker.claim()
        assert claim.document_id == second.id
    async with database.transaction() as session:
        record = await session.get(DocumentRecord, second.id)
        record.lease_until = now() - timedelta(seconds=1)
    await worker.claim()
    renewed = await worker.claim()
    assert (
        renewed.document_id == second.id and renewed.claim_id != claim.claim_id
    )
    chunks = [
        TextChunk(
            ordinal=0,
            text="second",
            start_offset=0,
            end_offset=6,
            token_count=1,
        )
    ]
    vectors = [[1.0] + [0.0] * 511]
    assert not await worker.publish(claim, (chunks, vectors))
    assert await worker.publish(renewed, (chunks, vectors))


async def test_real_hnsw_iterative_scan_stays_in_library_scope(database):
    base = await library(database)
    record = await document(database, base, status="ready")
    async with database.transaction() as session:
        version = await session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        if tuple(int(part) for part in version.split(".")[:2]) < (0, 8):
            pytest.skip("HNSW iterative scans require pgvector 0.8 or newer")
        session.add(
            ChunkRecord(
                document_id=record.id,
                ordinal=0,
                text="source",
                start_offset=0,
                end_offset=6,
                token_count=1,
                embedding=[1.0] + [0.0] * 511,
                index_key=record.index_key,
            )
        )
    async with database.transaction() as session:
        await session.execute(
            text("SET LOCAL hnsw.iterative_scan = 'strict_order'")
        )
        await session.execute(text("SET LOCAL hnsw.max_scan_tuples = 1000"))
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        rows = (
            await session.execute(
                search_statement(
                    base.id,
                    [1.0] + [0.0] * 511,
                    epoch=base.index_epoch,
                    fingerprint=profile()["fingerprint"],
                    limit=5,
                )
            )
        ).all()
    assert rows and all(row[0].document_id == record.id for row in rows)
