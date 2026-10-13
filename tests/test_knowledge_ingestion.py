from __future__ import annotations

import hashlib
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.capabilities.tools.contracts import ToolContext
from opsmesh.capabilities.tools.memory import KnowledgeCitationAccessError
from opsmesh.capabilities.tools.service import ProductToolService
from opsmesh.capabilities.tools.workspace_memory import WorkspaceMemorySearchService
from opsmesh.identity.authorization.errors import PermissionDeniedError
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.knowledge.activities import KnowledgeIngestionActivities
from opsmesh.resources.knowledge.content import KnowledgeURLFetcher
from opsmesh.resources.knowledge.ingestion import KnowledgeSourceIngestionService
from opsmesh.resources.knowledge.models import (
    KnowledgeCitation,
    KnowledgeSource,
    KnowledgeSourceIngestion,
)
from opsmesh.resources.knowledge.url_fetch import KnowledgeURLFetchError
from opsmesh.resources.memory.authorization import AuthorizedMemoryScope
from opsmesh.resources.memory.models import WorkspaceMemoryEntry
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace


def _execute_ingestion(session, ingestion, *, storage_root=".opsmesh-storage", fetcher=None):
    request = ExecutionRequest(
        workspace_id=ingestion.workspace_id,
        resource_id=ingestion.source_id,
        requested_by_user_id=ingestion.requested_by_user_id,
        kind=ExecutionKind.KNOWLEDGE_INGEST,
        parameters={"source_version": ingestion.source_version},
    )
    KnowledgeIngestionActivities(
        ActivityDependencies(
            sessionmaker(session.get_bind(), expire_on_commit=False),
            Settings(environment="test", storage_root=storage_root),
        ),
        fetcher or Mock(spec=KnowledgeURLFetcher),
    ).ingest(request)
    session.refresh(ingestion)
    return ingestion


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

    storage = LocalObjectStorage(str(tmp_path))
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
    result = _execute_ingestion(session, started, storage_root=str(tmp_path))
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
    product_results = ProductToolService(session).memory.search_workspace_memory(
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
    assert "quote" not in product_results[0]["citations"][0]
    assert "metadata" not in product_results[0]
    assert "ranking" not in product_results[0]
    memory_entry_id = UUID(str(product_results[0]["memory_entry_id"]))
    citation_result = ProductToolService(session).memory.get_knowledge_citations(
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
        ProductToolService(session).memory.get_knowledge_citations(
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
    session.commit()
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


def test_ingestion_route_stages_one_native_execution_and_accepts_url(tmp_path) -> None:
    client, session = _client()
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
    storage = LocalObjectStorage(str(tmp_path))
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
    row = session.scalars(select(TemporalOutboxEntry)).one()
    job = ExecutionRequest.model_validate(row.payload["execution"])
    assert job.kind is ExecutionKind.KNOWLEDGE_INGEST
    assert job.resource_id == UUID(source_id)
    assert job.parameters == {"source_version": 1}
    fetcher = Mock(spec=KnowledgeURLFetcher)
    activities = KnowledgeIngestionActivities(
        ActivityDependencies(
            sessionmaker(session.get_bind(), expire_on_commit=False),
            Settings(environment="test", storage_root=str(tmp_path)),
        ),
        fetcher,
    )
    activities.ingest(job)
    activities.ingest(job)
    fetcher.fetch.assert_not_called()
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
    accepted = client.post(
        f"{base}/{url_source.json()['id']}/ingest",
        headers=_headers(owner.id),
    )
    assert accepted.status_code == 202


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
    result = _execute_ingestion(session, started)
    session.commit()

    assert result.status == "failed"
    assert result.error_code == "unsupported_content_type"
    stored = session.get(KnowledgeSourceIngestion, ingestion.id)
    assert stored is not None
    assert stored.status == "failed"


def test_url_ingestion_uses_scoped_fetcher_and_records_citations() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    fetcher = Mock(spec=KnowledgeURLFetcher)
    fetcher.fetch.return_value = b"Fetched remote knowledge"
    base = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"
    source_response = client.post(
        base,
        headers=_headers(owner.id),
        json={
            "name": "Remote docs",
            "source_type": "url",
            "uri": "https://docs.example.com/guide",
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
    result = _execute_ingestion(session, started, fetcher=fetcher)
    session.commit()

    assert result.status == "succeeded"
    assert result.chunk_count == 1
    fetcher.fetch.assert_called_once()
    assert fetcher.fetch.call_args.kwargs["workspace_id"] == workspace.id
    assert fetcher.fetch.call_args.kwargs["url"] == "https://docs.example.com/guide"
    citation = session.scalar(
        select(KnowledgeCitation).where(KnowledgeCitation.ingestion_id == ingestion.id)
    )
    assert citation is not None
    assert citation.locator == "https://docs.example.com/guide"
    assert citation.quote == "Fetched remote knowledge"


@pytest.mark.parametrize("change", ["source_version", "actor_access", "fetch_failure"])
def test_native_url_transfer_has_no_database_connection_and_rechecks_live_authorization(
    tmp_path, change
):
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_headers(owner.id),
        json={
            "name": "Native URL",
            "source_type": "url",
            "uri": "https://docs.example.com/guide",
        },
    )
    assert response.status_code == 201
    source = session.get(KnowledgeSource, UUID(response.json()["id"]))
    ingestion = KnowledgeSourceIngestionService(session).request(
        workspace_id=workspace.id, source_id=source.id, requested_by_user_id=owner.id
    )
    session.commit()
    row = session.scalars(select(TemporalOutboxEntry)).one()
    request = ExecutionRequest.model_validate(row.payload["execution"])
    engine = session.get_bind()
    sessions = sessionmaker(engine, expire_on_commit=False)
    member_id = workspace.members[0].id
    session.commit()
    fetcher = Mock(spec=KnowledgeURLFetcher)

    def fetch(**kwargs):
        assert engine.pool.checkedout() == 0
        assert kwargs["workspace_id"] == workspace.id
        if change == "fetch_failure":
            raise KnowledgeURLFetchError("url_fetch_failed", "private-URL-canary")
        with sessions() as current:
            if change == "source_version":
                current.get(KnowledgeSource, source.id).version += 1
            else:
                current.get(WorkspaceMember, member_id).role = "viewer"
            current.commit()
        return b"Remote knowledge evidence"

    fetcher.fetch.side_effect = fetch
    activities = KnowledgeIngestionActivities(
        ActivityDependencies(sessions, Settings(environment="test", storage_root=str(tmp_path))),
        fetcher,
    )
    if change == "actor_access":
        with pytest.raises(PermissionDeniedError):
            activities.ingest(request)
    else:
        activities.ingest(request)
    session.expire_all()
    assert session.get(KnowledgeSourceIngestion, ingestion.id).status == "failed"
    assert not session.scalars(select(WorkspaceMemoryEntry)).all()
    assert "private-URL-canary" not in str(
        session.get(KnowledgeSourceIngestion, ingestion.id).error_code
    )
