"""Content changes, failed imports and revocation cannot serve stale embeddings."""

import asyncio

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import KnowledgeDocument
from opsmesh.modules.capabilities.internal.knowledge import LocalKnowledgeIndex
from opsmesh.modules.capabilities.knowledge import KnowledgeImportRequest, KnowledgeTextSource


async def allow(actor, resource):
    if actor != "owner":
        raise OpsMeshError("AUTHORIZATION_DENIED", "Denied")


class Embeddings:
    id = "semantic-v1"
    dimensions = 2
    fail = False

    async def embed(self, texts):
        if self.fail:
            raise OpsMeshError("EMBEDDING_UNAVAILABLE", "Offline")
        return tuple((1.0, 0.0) if "car" in text else (0.0, 1.0) for text in texts)


async def test_document_cas_never_reads_old_vector(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    embeddings = Embeddings()
    index = LocalKnowledgeIndex(database, allow, embeddings=embeddings)
    await database.initialize()
    try:
        document = KnowledgeDocument(id="doc", collection="docs", text="car")
        await index.add(document, principal_id="owner")
        await index.update_document(
            document.model_copy(update={"text": "boat"}), principal_id="owner", expected_revision=1
        )
        assert not await index.search("docs", "car", mode="vector", principal_id="owner")
        assert (await index.search("docs", "boat", mode="vector", principal_id="owner"))[
            0
        ].document.text == "boat"
        with pytest.raises(OpsMeshError):
            await index.update_document(document, principal_id="owner", expected_revision=1)
        assert (await index.get_document("doc", principal_id="owner")).text == "boat"
        embeddings.fail = True
        with pytest.raises(OpsMeshError):
            await index.update_document(document, principal_id="owner", expected_revision=2)
        assert (await index.get_document("doc", principal_id="owner")).text == "boat"
        await index.delete_document("doc", principal_id="owner", expected_revision=2)
        assert await index.list_documents("docs", principal_id="owner") == ()
    finally:
        await database.close()


async def test_import_jobs_resume_and_collection_tombstone_blocks_reuse(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    embeddings = Embeddings()
    index = LocalKnowledgeIndex(database, allow, embeddings=embeddings)
    await database.initialize()
    try:
        request = KnowledgeImportRequest(
            idempotency_key="batch",
            sources=(
                KnowledgeTextSource(
                    id="readme", format="markdown", text="# Title\n" + "car\n" * 90
                ),
            ),
            chunk_size=100,
            overlap=10,
        )
        job = await index.import_texts("docs", request, principal_id="owner")
        assert job.status == "succeeded" and job.completed > 1
        replay = await index.import_texts("docs", request, principal_id="owner")
        assert (
            replay.id == job.id
            and len(await index.list_documents("docs", principal_id="owner")) == job.completed
        )
        with pytest.raises(OpsMeshError):
            await index.import_texts(
                "docs", request.model_copy(update={"chunk_size": 200}), principal_id="owner"
            )
        rebuilt = await index.rebuild_embeddings(
            "docs", principal_id="owner", idempotency_key="rebuild"
        )
        assert rebuilt.completed == job.completed
        with pytest.raises(OpsMeshError):
            await index.get_maintenance_job(job.id, principal_id="outsider")
        await index.delete_collection("docs", principal_id="owner", expected_revision=1)
        with pytest.raises(OpsMeshError):
            await index.search("docs", "car", principal_id="owner")
        with pytest.raises(OpsMeshError):
            await index.add(
                KnowledgeDocument(id="new", collection="docs", text="car"), principal_id="owner"
            )
    finally:
        await database.close()


async def test_failed_import_is_durable_and_retries_resume_without_duplicate_chunks(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    embeddings = Embeddings()
    index = LocalKnowledgeIndex(database, allow, embeddings=embeddings)
    await database.initialize()
    try:
        request = KnowledgeImportRequest(
            idempotency_key="resume",
            sources=(KnowledgeTextSource(id="source", text="car " * 150),),
            chunk_size=100,
            overlap=0,
        )
        embeddings.fail = True
        with pytest.raises(OpsMeshError):
            await index.import_texts("docs", request, principal_id="owner")
        row = (await index._jobs.list())[0]
        assert (await index.get_maintenance_job(row.id, principal_id="owner")).status == "failed"
        embeddings.fail = False
        result = await index.import_texts("docs", request, principal_id="owner")
        assert result.status == "succeeded" and result.completed == 6
        assert len(await index.list_documents("docs", principal_id="owner")) == 6
    finally:
        await database.close()


async def test_ingestion_claim_excludes_a_second_worker(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingEmbedding(Embeddings):
        async def embed(self, texts):
            entered.set()
            await release.wait()
            return await super().embed(texts)

    index = LocalKnowledgeIndex(database, allow, embeddings=BlockingEmbedding())
    second = LocalKnowledgeIndex(database, allow, embeddings=Embeddings())
    await database.initialize()
    request = KnowledgeImportRequest(
        idempotency_key="concurrent", sources=(KnowledgeTextSource(id="source", text="car"),)
    )
    task = asyncio.create_task(index.import_texts("docs", request, principal_id="owner"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(OpsMeshError, match="already being processed"):
            await second.import_texts("docs", request, principal_id="owner")
        release.set()
        assert (await task).status == "succeeded"
        assert len(await second.list_documents("docs", principal_id="owner")) == 1
    finally:
        release.set()
        await task
        await database.close()
