from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import fakeredis
from sqlalchemy import select

from backend.app.core.common.config import Settings
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.knowledge.ingestion import KnowledgeSourceIngestionService
from backend.app.domains.knowledge.models import KnowledgeSource, KnowledgeSourceIngestion
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.storage import LocalStorage
from backend.app.runtime.workers.contracts import JobType
from backend.app.runtime.workers.execution.registry import WorkerJobHandler
from backend.app.runtime.workers.queue.redis import RedisQueue
from backend.tests.test_workspace_api import _client, _headers, _seed_workspace


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

    duplicate = service.request(
        workspace_id=workspace.id,
        source_id=source.id,
        requested_by_user_id=owner.id,
    )
    assert duplicate.id == ingestion.id
    assert duplicate.status == "succeeded"


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
    assert "isolated fetch runtime" in rejected.json()["error"]["message"]


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
