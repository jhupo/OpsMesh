from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import fakeredis
from sqlalchemy import select

from backend.app.core.common.config import Settings
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.domains.agents.memory.authorization import AuthorizedMemoryScope
from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.capabilities.tools.context import ToolContext
from backend.app.domains.capabilities.tools.memory import KnowledgeCitationAccessError
from backend.app.domains.capabilities.tools.service import ProductToolService
from backend.app.domains.capabilities.tools.workspace_memory import WorkspaceMemorySearchService
from backend.app.domains.knowledge.ingestion import KnowledgeSourceIngestionService
from backend.app.domains.knowledge.models import (
    KnowledgeCitation,
    KnowledgeSource,
    KnowledgeSourceIngestion,
)
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.storage import LocalStorage
from backend.app.runtime.environment.contracts import (
    RuntimeCommandInputFile,
    RuntimeCommandResult,
)
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.environment.url_fetch import RuntimeUrlFetcher
from backend.app.runtime.workers.contracts import JobType
from backend.app.runtime.workers.execution.registry import WorkerJobHandler
from backend.app.runtime.workers.queue import RedisQueue
from backend.tests.test_workspace_api import _client, _headers, _seed_workspace


class _UrlFetchDockerClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.commands: list[list[str]] = []

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult:
        _ = (container_id, timeout_seconds, input_file, working_dir)
        self.commands.append(command)
        return RuntimeCommandResult(exit_code=0, stdout="", stderr="")

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes:
        _ = (container_id, source_path, max_bytes, timeout_seconds)
        return self.content


def test_workspace_file_ingestion_materializes_versioned_memory_chunks(tmp_path) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    content = ("OpsMesh knowledge source content. " * 80).encode()
    storage_key = f"workspaces/{workspace.id}/files/{uuid4()}"
    workspace_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="guide.txt",
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
        file_metadata={},
    )
    session.add(workspace_file)
    session.commit()

    source_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_headers(owner.id),
        json={
            "name": "Guide",
            "source_type": "workspace_file",
            "workspace_file_id": str(workspace_file.id),
        },
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]

    storage = LocalStorage(str(tmp_path))
    storage.write(storage_key, content)
    source = session.get(KnowledgeSource, UUID(source_id))
    assert source is not None
    service = KnowledgeSourceIngestionService(session)
    ingestion = service.request(
        workspace_id=workspace.id,
        source_id=source.id,
        requested_by_user_id=owner.id,
    )
    session.commit()
    started = service.start(
        workspace_id=workspace.id,
        source_id=source.id,
        source_version=source.version,
    )
    assert started is not None
    session.commit()
    result = service.process(ingestion=started, storage=storage)
    session.commit()

    assert result.status == "succeeded"
    assert result.byte_count == len(content)
    assert result.chunk_count >= 2
    assert result.error_code is None
    assert started.content_sha256 == hashlib.sha256(content).hexdigest()
    entries = session.scalars(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.source_type == "knowledge_source",
            WorkspaceMemoryEntry.source_id == str(source.id),
            WorkspaceMemoryEntry.status == "active",
        )
    ).all()
    assert len(entries) == result.chunk_count
    assert {entry.memory_metadata["source_version"] for entry in entries} == {1}
    assert {entry.memory_metadata["filename"] for entry in entries} == {"guide.txt"}
    citations = client.get(
        f"/api/v1/workspaces/{workspace.id}/knowledge/sources/{source_id}/"
        f"ingestions/{ingestion.id}/citations",
        headers=_headers(owner.id),
    )
    assert citations.status_code == 200
    assert citations.json()["total"] == result.chunk_count
    assert citations.json()["items"][0]["locator"] == f"workspace-file://{workspace_file.id}"
    authorized = WorkspaceMemorySearchService(session).search(
        workspace_id=workspace.id,
        query="OpsMesh knowledge",
        source_types={"knowledge_source"},
        access_scopes=(
            AuthorizedMemoryScope(
                resource_id=workspace.id,
                access_mode="read",
                source_types=frozenset({"knowledge_source"}),
                scope_types=frozenset({"workspace"}),
                scope_ids=frozenset({str(workspace.id)}),
            ),
        ),
    )
    denied = WorkspaceMemorySearchService(session).search(
        workspace_id=workspace.id,
        query="OpsMesh knowledge",
        source_types={"knowledge_source"},
        access_scopes=(
            AuthorizedMemoryScope(
                resource_id=workspace.id,
                access_mode="read",
                source_types=frozenset({"workspace_memory"}),
            ),
        ),
    )
    assert authorized
    assert authorized[0]["source_type"] == "knowledge_source"
    assert denied == []
    product_context = ToolContext(
        workspace_id=workspace.id,
        agent_run_id=None,
        task_id=None,
        allowed_tools=frozenset({"search_workspace_memory", "get_knowledge_citations"}),
    )
    product_results = ProductToolService(session).search_workspace_memory(
        product_context,
        "OpsMesh knowledge",
        source_types={"knowledge_source"},
        access_scopes=(
            AuthorizedMemoryScope(
                resource_id=workspace.id,
                access_mode="read",
                source_types=frozenset({"knowledge_source"}),
                scope_types=frozenset({"workspace"}),
                scope_ids=frozenset({str(workspace.id)}),
            ),
        ),
    )
    assert product_results[0]["citations"]
    memory_entry_id = UUID(str(product_results[0]["metadata"]["memory_entry_id"]))
    citation_result = ProductToolService(session).get_knowledge_citations(
        product_context,
        memory_entry_id=memory_entry_id,
        access_scopes=(
            AuthorizedMemoryScope(
                resource_id=workspace.id,
                access_mode="read",
                source_types=frozenset({"knowledge_source"}),
                scope_types=frozenset({"workspace"}),
                scope_ids=frozenset({str(workspace.id)}),
            ),
        ),
    )
    assert citation_result["total"] == 1
    assert citation_result["items"][0]["memory_entry_id"] == str(memory_entry_id)
    try:
        ProductToolService(session).get_knowledge_citations(
            product_context,
            memory_entry_id=memory_entry_id,
            access_scopes=(
                AuthorizedMemoryScope(
                    resource_id=workspace.id,
                    access_mode="read",
                    source_types=frozenset({"workspace_memory"}),
                ),
            ),
        )
    except KnowledgeCitationAccessError as exc:
        assert "authorized resource scope" in str(exc)
    else:
        raise AssertionError("Expected citation retrieval to require knowledge source scope")

    duplicate = service.request(
        workspace_id=workspace.id,
        source_id=source.id,
        requested_by_user_id=owner.id,
    )
    assert duplicate.id == ingestion.id
    assert duplicate.status == "succeeded"
    source_path = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"
    archived = client.post(
        f"{source_path}/{source_id}/archive",
        headers=_headers(owner.id),
        json={"expected_version": 1},
    )
    assert archived.status_code == 200
    assert (
        WorkspaceMemorySearchService(session).search(
            workspace_id=workspace.id,
            query="OpsMesh knowledge",
            source_types={"knowledge_source"},
            access_scopes=(
                AuthorizedMemoryScope(
                    resource_id=workspace.id,
                    access_mode="read",
                    source_types=frozenset({"knowledge_source"}),
                    scope_types=frozenset({"workspace"}),
                    scope_ids=frozenset({str(workspace.id)}),
                ),
            ),
        )
        == []
    )


def test_ingestion_route_enqueues_idempotent_job_and_rejects_url(tmp_path) -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("test"),
        queue_name="worker",
        blocking_timeout_seconds=0,
    )
    client, session = _client(queue)
    owner, workspace = _seed_workspace(session, role="owner")
    content = b"Short knowledge source"
    storage_key = f"workspaces/{workspace.id}/files/{uuid4()}"
    workspace_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="notes.md",
        content_type="text/markdown",
        size_bytes=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
        file_metadata={},
    )
    session.add(workspace_file)
    session.commit()
    storage = LocalStorage(str(tmp_path))
    storage.write(storage_key, content)

    base = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"
    source = client.post(
        base,
        headers=_headers(owner.id),
        json={
            "name": "Notes",
            "source_type": "workspace_file",
            "workspace_file_id": str(workspace_file.id),
        },
    )
    assert source.status_code == 201
    source_id = source.json()["id"]
    first = client.post(f"{base}/{source_id}/ingest", headers=_headers(owner.id))
    second = client.post(f"{base}/{source_id}/ingest", headers=_headers(owner.id))

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    job = queue.dequeue()
    assert job is not None
    assert job.job_type == JobType.KNOWLEDGE_INGEST
    assert job.resource_id == UUID(source_id)
    assert job.routing == {"source_version": 1}
    assert queue.dequeue() is None

    worker = WorkerJobHandler(
        session,
        queue,
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token="test-token",
            database_url="sqlite+pysqlite:///:memory:",
            storage_root=str(tmp_path),
        ),
    )
    worker.handle(job)
    queue.ack(job)
    status_response = client.get(
        f"{base}/{source_id}/ingestions/{first.json()['id']}",
        headers=_headers(owner.id),
    )
    history_response = client.get(
        f"{base}/{source_id}/ingestions",
        headers=_headers(owner.id),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert status_response.json()["chunk_count"] == 1
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 1

    url_source = client.post(
        base,
        headers=_headers(owner.id),
        json={"name": "Remote", "source_type": "url", "uri": "https://example.com"},
    )
    assert url_source.status_code == 201
    rejected = client.post(
        f"{base}/{url_source.json()['id']}/ingest",
        headers=_headers(owner.id),
    )
    assert rejected.status_code == 400
    assert "fetch_runtime_id" in rejected.json()["error"]["message"]


def test_ingestion_failure_is_recorded_for_unsupported_file() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    file_id = uuid4()
    storage_key = f"workspaces/{workspace.id}/files/{file_id}"
    workspace_file = WorkspaceFile(
        id=file_id,
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="archive.bin",
        content_type="application/octet-stream",
        size_bytes=4,
        checksum_sha256="a" * 64,
        storage_key=storage_key,
        file_metadata={},
    )
    session.add(workspace_file)
    session.commit()
    source_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_headers(owner.id),
        json={
            "name": "Binary",
            "source_type": "workspace_file",
            "workspace_file_id": str(file_id),
        },
    )
    assert source_response.status_code == 201
    source = session.get(KnowledgeSource, UUID(source_response.json()["id"]))
    assert source is not None
    service = KnowledgeSourceIngestionService(session)
    ingestion = service.request(
        workspace_id=workspace.id,
        source_id=source.id,
        requested_by_user_id=owner.id,
    )
    session.commit()
    started = service.start(
        workspace_id=workspace.id,
        source_id=source.id,
        source_version=1,
    )
    assert started is not None
    session.commit()
    result = service.process(ingestion=started, storage=LocalStorage("."))
    session.commit()

    assert result.status == "failed"
    assert result.error_code == "unsupported_content_type"
    stored = session.get(KnowledgeSourceIngestion, ingestion.id)
    assert stored is not None
    assert stored.status == "failed"


def test_url_ingestion_uses_network_enabled_runtime_and_records_citations() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    docker = _UrlFetchDockerClient(b"Fetched remote knowledge")
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Knowledge fetch runtime",
        status="running",
        connection_status="online",
        docker_container_id="fetch-container",
        execution_mode="isolated",
        limits={"timeout_seconds": 60, "max_output_bytes": 256_000},
        network_policy={"mode": "internet", "disabled": False},
        capabilities={},
    )
    session.add(runtime)
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"
    source_response = client.post(
        base,
        headers=_headers(owner.id),
        json={
            "name": "Remote docs",
            "source_type": "url",
            "uri": "https://docs.example.com/guide",
            "config": {"fetch_runtime_id": str(runtime.id)},
        },
    )
    assert source_response.status_code == 201
    source_id = UUID(source_response.json()["id"])
    service = KnowledgeSourceIngestionService(session)
    ingestion = service.request(
        workspace_id=workspace.id,
        source_id=source_id,
        requested_by_user_id=owner.id,
    )
    session.commit()
    started = service.start(
        workspace_id=workspace.id,
        source_id=source_id,
        source_version=1,
    )
    assert started is not None
    session.commit()
    result = service.process(
        ingestion=started,
        storage=None,
        url_fetcher=RuntimeUrlFetcher(session, docker),
    )
    session.commit()

    assert result.status == "succeeded"
    assert result.chunk_count == 1
    assert len(docker.commands) == 2
    citation = session.scalar(
        select(KnowledgeCitation).where(KnowledgeCitation.ingestion_id == ingestion.id)
    )
    assert citation is not None
    assert citation.locator == "https://docs.example.com/guide"
    assert citation.quote == "Fetched remote knowledge"
