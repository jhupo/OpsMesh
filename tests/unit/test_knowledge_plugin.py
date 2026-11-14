"""Deterministic knowledge persistence, publication, and native tool tests."""

# ruff: noqa: E402

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
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
from opsmesh.plugins.model import Plugin, PluginStatus

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
    knowledge = pytest.importorskip(
        "opsmesh.plugins.knowledge", exc_type=ImportError
    )
finally:
    driver_state._driver = previous_driver
    registry._plugins = previous_plugins

from agents import Agent, RunConfig, Runner
from agents.testing import ScriptedModel
from agents.testing.model import ModelStep, assistant_message
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import JSON, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from opsmesh.plugins.knowledge import indexing, lifecycle
from opsmesh.plugins.knowledge.config import (
    MODEL_NAME,
    QUERY_INSTRUCTION,
    Settings,
)
from opsmesh.plugins.knowledge.embeddings import (
    Encoder,
    inspect_manifest,
    load_model,
    validate_vector,
)
from opsmesh.plugins.knowledge.errors import (
    EncodingBusy,
    IndexUnavailable,
    InvalidDocument,
    InvalidQuery,
    KnowledgeNotFound,
    ModelConfigurationError,
    StaleReference,
)
from opsmesh.plugins.knowledge.indexing import IndexWorker, claim_statement
from opsmesh.plugins.knowledge.knowledge_bases import KnowledgeBase
from opsmesh.plugins.knowledge.models import (
    ChunkRecord,
    DocumentRecord,
    KnowledgeBaseRecord,
    now,
)
from opsmesh.plugins.knowledge.schemas import IndexProfile, SearchHit
from opsmesh.plugins.knowledge.search import search_statement
from opsmesh.plugins.knowledge.text import normalize, query_text, split_text


@compiles(JSONB, "sqlite")
def sqlite_jsonb(element, compiler, **kwargs):
    return compiler.visit_JSON(JSON(), **kwargs)


class CharacterTokenizer:
    """Model-free tokenizer with exact non-whitespace character offsets."""

    is_fast = True

    def __call__(
        self,
        value,
        *,
        add_special_tokens=True,
        return_offsets_mapping=False,
        truncation=False,
    ):
        offsets = [
            (index, index + 1)
            for index, char in enumerate(value)
            if not char.isspace()
        ]
        result = {
            "input_ids": list(
                range(len(offsets) + (2 if add_special_tokens else 0))
            )
        }
        if return_offsets_mapping:
            result["offset_mapping"] = offsets
        return result


class FakeModel:
    """Return normalized 512-dimensional deterministic vectors."""

    tokenizer = CharacterTokenizer()

    def __init__(self):
        self.inputs = []

    def encode(self, values, **kwargs):
        self.inputs.extend(values)
        return [[1.0] + [0.0] * 511 for _ in values]


def profile(settings=None):
    settings = settings or Settings()
    return IndexProfile(
        revision=settings.model_revision,
        manifest_digest="a" * 64,
        tokenizer_digest="b" * 64,
        chunk_tokens=settings.chunk_tokens,
        overlap_tokens=settings.overlap_tokens,
    )


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    # SQLite strips timezone data and lacks PostgreSQL's clock_timestamp().
    monkeypatch.setattr(
        indexing,
        "database_now",
        AsyncMock(
            side_effect=lambda session: datetime.now(UTC).replace(tzinfo=None)
        ),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        KnowledgeBaseRecord.__table__,
        DocumentRecord.__table__,
        ChunkRecord.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: KnowledgeBaseRecord.metadata.create_all(
                sync, tables=tables
            )
        )
        await connection.execute(
            __import__("sqlalchemy").text("PRAGMA foreign_keys = ON")
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction():
        async with sessions() as session, session.begin():
            yield session

    owner = Plugin("knowledge", knowledge, knowledge.__name__, None)
    caller = Plugin("consumer", ModuleType("consumer"), "consumer", None)
    stranger = Plugin("stranger", ModuleType("stranger"), "stranger", None)
    for plugin in (owner, caller, stranger):
        plugin.status = PluginStatus.READY
    caller.dependencies = stranger.dependencies = [owner.id_]
    monkeypatch.setattr(
        registry,
        "_plugins",
        {item.id_: item for item in (owner, caller, stranger)},
    )
    settings = Settings(chunk_tokens=32, overlap_tokens=4)
    resource = lifecycle.KnowledgeRuntime(settings, owner)
    resource.database = SimpleNamespace(
        session=sessions, transaction=transaction
    )
    resource.encoder = Encoder(FakeModel(), profile(settings), settings)
    resource.data = tmp_path
    resource.accepting = True
    resource.caller = caller
    resource.stranger = stranger
    resource.file_owners = []

    async def write_file(path, content, **kwargs):
        resource.file_owners.append(registry.get_current_plugin())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    async def delete_file(path):
        path.unlink(missing_ok=True)

    resource.filesystem = SimpleNamespace(
        write_file=write_file, delete=delete_file
    )
    monkeypatch.setattr(lifecycle, "_runtime", resource)
    try:
        with registry.plugin_scope(caller):
            yield resource
    finally:
        await resource.shutdown()
        await engine.dispose()


@pytest.mark.parametrize("content", [b"", b"  \r\n", b"\xff", b"a\x00b"])
def test_invalid_sources_are_rejected(content):
    with pytest.raises(InvalidDocument):
        normalize(content, Settings())


def test_only_bom_and_line_endings_are_normalized():
    source = "\ufeff\u4e2d\u6587\r\n# Title\rbody\t  "
    assert (
        normalize(source.encode(), Settings())
        == "\u4e2d\u6587\n# Title\nbody\t  "
    )


def test_source_byte_limit_is_enforced_before_decoding():
    with pytest.raises(InvalidDocument):
        normalize(b"abcd", Settings(max_document_bytes=3))


def test_long_paragraph_chunks_overlap_and_keep_exact_offsets():
    settings = Settings(chunk_tokens=32, overlap_tokens=8)
    source = "\u4e2d\u6587\u6587\u6863" * 40
    chunks = split_text(source, CharacterTokenizer(), settings)
    assert len(chunks) > 1
    assert all(
        chunk.text == source[chunk.start_offset : chunk.end_offset]
        for chunk in chunks
    )
    assert all(chunk.token_count <= 32 for chunk in chunks)
    assert chunks[-1].end_offset == len(source)
    assert chunks[1].start_offset == chunks[0].end_offset - 8


def test_paragraph_boundary_is_preferred_inside_token_window():
    text = "a" * 20 + "\n\n" + "b" * 40
    chunks = split_text(
        text, CharacterTokenizer(), Settings(chunk_tokens=32, overlap_tokens=0)
    )
    assert chunks[0].text == "a" * 20


def test_query_limit_counts_instruction_and_special_tokens():
    tokenizer = CharacterTokenizer()
    prefix_tokens = len(
        tokenizer(QUERY_INSTRUCTION, add_special_tokens=False)["input_ids"]
    )
    query = "a" * (512 - 2 - prefix_tokens)
    assert query_text(query, tokenizer, QUERY_INSTRUCTION).endswith(query)
    with pytest.raises(InvalidQuery):
        query_text(query + "a", tokenizer, QUERY_INSTRUCTION)


@pytest.mark.parametrize("query", ["", " ", "a\x00b"])
def test_invalid_queries_are_rejected(query):
    with pytest.raises(InvalidQuery):
        query_text(query, CharacterTokenizer(), QUERY_INSTRUCTION)


def test_profile_changes_for_chunk_settings():
    assert (
        profile().fingerprint != profile(Settings(chunk_tokens=320)).fingerprint
    )


@pytest.mark.parametrize("vector", [[0.0] * 512, [1.0], [float("nan")] * 512])
def test_encoder_rejects_invalid_vectors(vector):
    with pytest.raises(ModelConfigurationError):
        validate_vector(vector)


def test_manifest_rejects_modified_files(tmp_path):
    files = {"config.json": b"{}", "tokenizer.json": b"{}"}
    for name, value in files.items():
        (tmp_path / name).write_bytes(value)
    manifest = {
        "model": MODEL_NAME,
        "revision": Settings().model_revision,
        "files": {
            name: hashlib.sha256(value).hexdigest()
            for name, value in files.items()
        },
    }
    (tmp_path / "opsmesh-model-manifest.json").write_text(json.dumps(manifest))
    assert len(inspect_manifest(tmp_path, Settings().model_revision)[0]) == 64
    (tmp_path / "config.json").write_bytes(b"changed")
    with pytest.raises(ModelConfigurationError):
        inspect_manifest(tmp_path, Settings().model_revision)


@pytest.mark.anyio
async def test_normalized_vectors_and_document_query_prefixes():
    model = FakeModel()
    encoder = Encoder(model, profile(), Settings())
    chunks, vectors = await encoder.documents("document source")
    await encoder.query("question")
    assert model.inputs == [chunks[0].text, QUERY_INSTRUCTION + "question"]
    assert (
        len(vectors[0]) == 512
        and sum(value * value for value in vectors[0]) == 1
    )
    await encoder.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancellation_keeps_model_until_thread_finishes(worker_fails):
    entered = threading.Event()
    release = threading.Event()
    model = FakeModel()

    def blocking(values, **kwargs):
        entered.set()
        release.wait(5)
        if worker_fails:
            raise RuntimeError("Encoding failed after cancellation")
        return [[1.0] + [0.0] * 511]

    model.encode = blocking
    encoder = Encoder(model, profile(), Settings(max_pending_encodings=1))
    task = asyncio.create_task(encoder.query("question"))
    try:
        await anyio.to_thread.run_sync(entered.wait, 2)
        task.cancel()
        closing = asyncio.create_task(encoder.shutdown())
        await asyncio.sleep(0)
        assert encoder.model is model and not closing.done() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await closing
        assert encoder.model is None
    finally:
        release.set()


@pytest.mark.anyio
async def test_encoder_queue_is_bounded():
    entered = threading.Event()
    release = threading.Event()
    model = FakeModel()

    def blocking(values, **kwargs):
        entered.set()
        release.wait(5)
        return [[1.0] + [0.0] * 511]

    model.encode = blocking
    encoder = Encoder(model, profile(), Settings(max_pending_encodings=1))
    task = asyncio.create_task(encoder.query("question"))
    try:
        await anyio.to_thread.run_sync(entered.wait, 2)
        with pytest.raises(EncodingBusy):
            await encoder.query("another")
    finally:
        release.set()
        await task
        await encoder.shutdown()


@pytest.mark.anyio
async def test_library_lookup_and_listing_are_owned(runtime):
    base = await knowledge.create("Owned")
    assert (await knowledge.get(base.id)).id == base.id
    with registry.plugin_scope(runtime.stranger):
        assert await knowledge.list() == []
        with pytest.raises(KnowledgeNotFound):
            await knowledge.get(base.id)


@pytest.mark.anyio
async def test_source_files_remain_in_knowledge_scope(runtime):
    base = await knowledge.create("Owned")
    document = await base.documents.import_file(
        b"source\r\ntext", name="../../display.md"
    )
    async with runtime.database.session() as session:
        record = await session.get(DocumentRecord, document.id)
        assert record.text == "source\ntext"
        assert (
            runtime.data / record.source_path
        ).read_bytes() == b"source\r\ntext"
        assert "display" not in record.source_path
    assert runtime.file_owners == [runtime.owner]


@pytest.mark.anyio
async def test_failed_document_transaction_cleans_published_source(
    runtime, monkeypatch
):
    base = await knowledge.create("Owned")
    original = runtime.database.transaction

    @asynccontextmanager
    async def rejected():
        async with original() as session:
            yield session
            raise RuntimeError("transaction rejected")

    monkeypatch.setattr(runtime.database, "transaction", rejected)
    with pytest.raises(RuntimeError, match="transaction rejected"):
        await base.documents.add("source", name="name")
    assert list(runtime.data.rglob("*.txt")) == []
    assert await base.documents.list() == []


@pytest.mark.anyio
async def test_worker_publishes_complete_chunks_and_references(runtime):
    base = await knowledge.create("Owned")
    document = await base.documents.add("a" * 80, name="name")
    worker = IndexWorker(runtime)
    claim = await worker.claim()
    await worker.process(claim)
    ready = await base.documents.get(document.id)
    assert (
        ready.status == "ready"
        and ready.indexed_profile == runtime.encoder.profile.fingerprint
    )
    async with runtime.database.session() as session:
        chunks = list(
            await session.scalars(
                select(ChunkRecord).where(
                    ChunkRecord.document_id == document.id
                )
            )
        )
        assert len(chunks) > 1
        assert all(chunk.index_key == ready.index_key for chunk in chunks)
    read = await base.documents.read(
        document.id, start=10, end=20, index_key=ready.index_key
    )
    assert read.text == "a" * 10


@pytest.mark.anyio
async def test_update_invalidates_old_worker_and_citation(runtime):
    base = await knowledge.create("Owned")
    document = await base.documents.add("old source", name="name")
    worker = IndexWorker(runtime)
    stale = await worker.claim()
    await base.documents.update(document.id, "replacement")
    result = await runtime.encoder.documents(stale.text)
    assert not await worker.publish(stale, result)
    with pytest.raises(StaleReference):
        await base.documents.read(document.id, index_key=document.index_key)
    assert (await base.documents.get(document.id)).status == "pending"


@pytest.mark.anyio
async def test_deleted_document_rejects_inflight_publication(runtime):
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    worker = IndexWorker(runtime)
    stale = await worker.claim()
    await base.documents.delete(document.id)
    assert not await worker.publish(
        stale, await runtime.encoder.documents(stale.text)
    )
    assert await base.documents.list() == []


@pytest.mark.anyio
async def test_expired_lease_is_reclaimed_and_old_claim_cannot_publish(runtime):
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    worker = IndexWorker(runtime)
    stale = await worker.claim()
    async with runtime.database.transaction() as session:
        record = await session.get(DocumentRecord, document.id)
        record.lease_until = now() - timedelta(seconds=1)
    current = await worker.claim()
    assert current.claim_id != stale.claim_id
    vectors = await runtime.encoder.documents(stale.text)
    assert not await worker.publish(stale, vectors)
    assert await worker.publish(current, vectors)


@pytest.mark.anyio
async def test_rebuild_remains_closed_until_current_epoch_documents_complete(
    runtime,
):
    base = await knowledge.create("Owned")
    first = await base.documents.add("first", name="first")
    worker = IndexWorker(runtime)
    old = await worker.claim()
    previous = (await base.info()).active_profile
    runtime.encoder.profile = profile(
        Settings(chunk_tokens=24, overlap_tokens=4)
    )
    rebuilding = await base.reindex()
    repeated = await base.reindex()
    assert repeated.index_epoch == rebuilding.index_epoch
    assert not await worker.publish(
        old, await runtime.encoder.documents(old.text)
    )
    second = await base.documents.add("second", name="second")
    assert second.index_epoch == rebuilding.index_epoch
    with pytest.raises(IndexUnavailable):
        await base.search("question")
    claim = await worker.claim()
    await worker.process(claim)
    assert (await base.info()).active_profile == previous
    claim = await worker.claim()
    await worker.process(claim)
    completed = await base.info()
    assert (
        completed.index_status == "ready"
        and completed.active_profile == runtime.profile
    )
    assert (
        await base.documents.get(first.id)
    ).indexed_profile == runtime.encoder.profile.fingerprint


@pytest.mark.anyio
async def test_empty_rebuild_can_complete_immediately(runtime):
    base = await knowledge.create("Empty")
    runtime.encoder.profile = profile(
        Settings(chunk_tokens=24, overlap_tokens=4)
    )
    result = await base.reindex()
    assert (
        result.index_status == "ready"
        and result.active_profile == runtime.profile
    )


@pytest.mark.anyio
async def test_failed_rebuild_retry_preserves_epoch(runtime):
    base = await knowledge.create("Owned")
    await base.documents.add("source", name="name")
    rebuilding = await base.reindex()
    worker = IndexWorker(runtime)
    claim = await worker.claim()
    await worker.fail(claim, InvalidDocument("bad format"))
    assert (await base.info()).index_status == "failed"
    retry = await base.reindex()
    assert retry.index_epoch == rebuilding.index_epoch
    assert (await base.documents.list())[0].attempts == 0
    await worker.process(await worker.claim())
    assert (await base.info()).index_status == "ready"


@pytest.mark.anyio
async def test_foreign_document_id_is_never_read(runtime):
    first = await knowledge.create("First")
    second = await knowledge.create("Second")
    document = await first.documents.add("source", name="name")
    with pytest.raises(KnowledgeNotFound):
        await second.documents.read(document.id)


def test_postgresql_search_contains_scope_and_profile_before_limit():
    base_id, epoch = uuid4(), uuid4()
    statement = search_statement(
        base_id,
        [1.0] + [0.0] * 511,
        epoch=epoch,
        fingerprint="profile",
        limit=7,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "knowledge_documents.knowledge_base_id =" in sql
    assert "knowledge_documents.indexed_profile =" in sql
    assert "knowledge_documents.index_epoch =" in sql
    assert "knowledge_chunks.index_key = knowledge_documents.index_key" in sql
    assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert (
        base_id in compiled.params.values()
        and epoch in compiled.params.values()
    )


def test_postgresql_claim_uses_skip_locked_and_target_profile():
    sql = str(
        claim_statement(profile().persisted(), now()).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "FOR UPDATE OF knowledge_documents SKIP LOCKED" in sql
    assert "knowledge_bases.target_profile =" in sql
    assert (
        "knowledge_documents.index_epoch = knowledge_bases.index_epoch" in sql
    )


@pytest.mark.anyio
async def test_native_tools_use_bound_scope_and_require_management_approval(
    runtime,
):
    base = await knowledge.create("Owned")
    tools = base.tools
    management = base.management_tools
    assert all(tool.needs_approval is False for tool in tools)
    assert all(tool.needs_approval is True for tool in management)
    schema = tools[0].params_json_schema
    assert set(schema["properties"]) == {"query", "limit"}
    with pytest.raises(ValueError, match="unique"):
        knowledge.unique_tools(tools, tools)
    assert len(knowledge.unique_tools(tools, management)) == 7


@pytest.mark.anyio
async def test_native_runner_calls_knowledge_tool_and_receives_source(
    runtime, monkeypatch
):
    base = await knowledge.create("Owned")
    hit = SearchHit(
        document_id=uuid4(),
        chunk_id=uuid4(),
        title="Source",
        text="Approved source text",
        start_offset=0,
        end_offset=20,
        distance=0.1,
        index_key=uuid4(),
    )
    search = AsyncMock(return_value=[hit])
    monkeypatch.setattr(KnowledgeBase, "search", search)
    tool = base.tools[0]
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    ResponseFunctionToolCall(
                        id="fc_1",
                        call_id="call_1",
                        type="function_call",
                        name=tool.name,
                        arguments=json.dumps({"query": "question", "limit": 1}),
                    )
                ]
            ),
            ModelStep(
                output=[assistant_message("The source states the answer.")]
            ),
        ]
    )
    result = await Runner.run(
        Agent(name="Knowledge test", model=model, tools=[tool]),
        "question",
        run_config=RunConfig(tracing_disabled=True),
    )
    assert result.final_output == "The source states the answer."
    search.assert_awaited_once_with("question", limit=1)
    assert "Approved source text" in str(model.calls[1])


def test_local_loader_never_resolves_a_remote_model(tmp_path, monkeypatch):
    files = {"config.json": b"{}", "tokenizer.json": b"{}"}
    for name, value in files.items():
        (tmp_path / name).write_bytes(value)
    manifest = {
        "model": MODEL_NAME,
        "revision": Settings().model_revision,
        "files": {
            name: hashlib.sha256(value).hexdigest()
            for name, value in files.items()
        },
    }
    (tmp_path / "opsmesh-model-manifest.json").write_text(json.dumps(manifest))
    calls = []
    model = FakeModel()
    model.get_sentence_embedding_dimension = lambda: 512

    def construct(path, **kwargs):
        calls.append((path, kwargs))
        return model

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(set_num_threads=lambda value: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=construct),
    )
    loaded, identity = load_model(tmp_path, Settings())
    assert loaded is model and identity.revision == Settings().model_revision
    assert calls[0][0] == str(tmp_path)
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["trust_remote_code"] is False


@pytest.mark.anyio
async def test_indexing_renews_lease_before_publishing_a_long_encoding(
    runtime, monkeypatch
):
    runtime.settings = runtime.settings.model_copy(
        update={"lease_seconds": 0.3}
    )
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    worker = IndexWorker(runtime)
    renewed = asyncio.Event()
    original = worker.renew
    original_encode = runtime.encoder.documents

    async def renew(claim):
        success = await original(claim)
        renewed.set()
        return success

    async def delayed(text):
        await asyncio.wait_for(renewed.wait(), 2)
        return await original_encode(text)

    monkeypatch.setattr(worker, "renew", renew)
    monkeypatch.setattr(runtime.encoder, "documents", delayed)
    await worker.process(await worker.claim())
    assert (
        renewed.is_set()
        and (await base.documents.get(document.id)).status == "ready"
    )


@pytest.mark.anyio
async def test_abandoned_final_claim_marks_rebuild_failed_on_recovery(runtime):
    runtime.settings = runtime.settings.model_copy(update={"max_attempts": 1})
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    await base.reindex()
    worker = IndexWorker(runtime)
    await worker.claim()
    async with runtime.database.transaction() as session:
        record = await session.get(DocumentRecord, document.id)
        record.lease_until = now() - timedelta(seconds=1)
    assert await worker.claim() is None
    await worker.recover_bases()
    assert (await base.info()).index_status == "failed"
    assert (await base.documents.get(document.id)).status == "failed"


@pytest.mark.anyio
async def test_orphan_cleanup_preserves_recent_files_and_all_references(
    runtime,
):
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    async with runtime.database.session() as session:
        record = await session.get(DocumentRecord, document.id)
        referenced = runtime.data / record.source_path
    old = runtime.data / "sources" / "old.txt"
    fresh = runtime.data / "sources" / "fresh.txt"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    expired = time.time() - runtime.settings.orphan_grace_seconds - 10
    os.utime(old, (expired, expired))
    os.utime(referenced, (expired, expired))
    await runtime.clean_orphans()
    assert not old.exists() and fresh.exists() and referenced.exists()


@pytest.mark.anyio
async def test_failed_file_deletion_does_not_restore_document_visibility(
    runtime, monkeypatch
):
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    monkeypatch.setattr(
        runtime.filesystem, "delete", AsyncMock(side_effect=OSError("busy"))
    )
    await base.documents.delete(document.id)
    assert await base.documents.list() == []


@pytest.mark.anyio
async def test_search_rechecks_profile_after_query_encoding(
    runtime, monkeypatch
):
    base = await knowledge.create("Owned")

    async def changed(query):
        await base.reindex()
        async with runtime.database.transaction() as session:
            record = await session.get(KnowledgeBaseRecord, base.id)
            record.index_status = "rebuilding"
        return [1.0] + [0.0] * 511

    monkeypatch.setattr(runtime.encoder, "query", changed)
    with pytest.raises(IndexUnavailable):
        await base.search("question")


@pytest.mark.anyio
async def test_native_management_approval_suspends_before_document_write(
    runtime,
):
    base = await knowledge.create("Owned")
    tool = base.management_tools[0]
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    ResponseFunctionToolCall(
                        id="fc_add",
                        call_id="call_add",
                        type="function_call",
                        name=tool.name,
                        arguments=json.dumps(
                            {"name": "approved.md", "text": "Approved source"}
                        ),
                    )
                ]
            ),
            ModelStep(output=[assistant_message("Accepted for indexing.")]),
        ]
    )
    agent = Agent(name="Approval test", model=model, tools=[tool])
    options = RunConfig(tracing_disabled=True)
    pending = await Runner.run(agent, "add document", run_config=options)
    assert len(pending.interruptions) == 1
    assert await base.documents.list() == []
    state = pending.to_state()
    state.approve(pending.interruptions[0])
    result = await Runner.run(agent, state, run_config=options)
    assert result.final_output == "Accepted for indexing."
    documents = await base.documents.list()
    assert len(documents) == 1 and documents[0].status == "pending"


@pytest.mark.anyio
async def test_invalid_unicode_text_never_writes_a_source(runtime):
    base = await knowledge.create("Owned")
    with pytest.raises(InvalidDocument):
        await base.documents.add("bad\ud800text", name="bad")
    assert list(runtime.data.rglob("*.txt")) == []


@pytest.mark.anyio
async def test_publication_uses_database_clock_after_worker_clock_changes(
    runtime, monkeypatch
):
    base = await knowledge.create("Owned")
    document = await base.documents.add("source", name="name")
    worker = IndexWorker(runtime)
    claim = await worker.claim()
    monkeypatch.setattr(indexing, "now", lambda: now() + timedelta(days=365))
    assert await worker.publish(
        claim, await runtime.encoder.documents(claim.text)
    )
    assert (await base.documents.get(document.id)).status == "ready"


@pytest.mark.anyio
async def test_stop_timeout_retains_worker_until_retry(runtime, monkeypatch):
    base = await knowledge.create("Owned")
    await base.documents.add("source", name="name")
    entered = asyncio.Event()
    release = asyncio.Event()
    encode = runtime.encoder.documents

    async def blocking(text):
        entered.set()
        await release.wait()
        return await encode(text)

    monkeypatch.setattr(runtime.encoder, "documents", blocking)
    await runtime.start_worker()
    worker = runtime.worker_task
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(TimeoutError), anyio.fail_after(0.01):
            await runtime.stop()
        assert runtime.worker_task is worker and not worker.done()
        assert runtime.encoder is not None
        release.set()
        await runtime.shutdown()
        assert runtime.worker_task is None and runtime.encoder is None
    finally:
        release.set()
        await runtime.shutdown()


@pytest.mark.anyio
async def test_cancelled_worker_does_not_poison_cleanup_retry(runtime):
    runtime.worker_task = asyncio.create_task(asyncio.sleep(10))
    runtime.worker_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runtime.stop()
    await runtime.shutdown()
    assert runtime.worker_task is None and runtime.encoder is None


@pytest.mark.anyio
async def test_startup_cancellation_waits_for_model_loader(
    runtime, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking(directory, settings):
        entered.set()
        try:
            if not release.wait(5):
                raise RuntimeError("Test did not release the loader")
            return FakeModel(), profile(settings)
        finally:
            finished.set()

    providers = {
        "database": SimpleNamespace(get_database=lambda: runtime.database),
        "filesystem": SimpleNamespace(
            data_dir=lambda: runtime.data, cache_dir=lambda: runtime.data
        ),
    }
    monkeypatch.setattr(lifecycle, "require", providers.__getitem__)
    monkeypatch.setattr(lifecycle, "load_model", blocking)
    starting = lifecycle.KnowledgeRuntime(runtime.settings, runtime.owner)
    task = asyncio.create_task(starting.startup())
    try:
        assert await anyio.to_thread.run_sync(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await starting.shutdown()
        assert finished.is_set() and starting.encoder is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_consumer_resolves_knowledge_during_framework_ready(runtime):
    from opsmesh.lifecycle import Lifespan

    base = await knowledge.create("Owned")
    lifespan = Lifespan()
    resolved = []

    async def prepare():
        selected = await knowledge.get(base.id)
        resolved.append(Agent(name="Support", tools=selected.tools))

    for plugin in registry._plugins.values():
        plugin.status = PluginStatus.LOADED
    with registry.plugin_scope(runtime.caller):
        lifespan.on_ready(prepare)
    try:
        await lifespan.startup()
        assert runtime.caller.ready
        assert len(resolved) == 1 and len(resolved[0].tools) == 3
    finally:
        await lifespan.shutdown()
