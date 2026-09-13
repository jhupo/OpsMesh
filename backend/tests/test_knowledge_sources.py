from uuid import uuid4

from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.tests.test_workspace_api import _client, _headers, _seed_workspace


def test_knowledge_source_lifecycle_is_versioned_and_workspace_scoped() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    path = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"

    created = client.post(
        path,
        headers=_headers(owner.id),
        json={
            "name": " Product docs ",
            "description": "  Public reference  ",
            "source_type": "url",
            "uri": "https://docs.example.com/guide?lang=en",
            "config": {"refresh": "manual"},
        },
    )
    assert created.status_code == 201
    source = created.json()
    assert source["name"] == "Product docs"
    assert source["description"] == "Public reference"
    assert source["version"] == 1
    assert source["status"] == "active"
    assert source["uri"] == "https://docs.example.com/guide?lang=en"

    updated = client.patch(
        f"{path}/{source['id']}",
        headers=_headers(owner.id),
        json={"expected_version": 1, "name": "Product documentation"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["source_fingerprint"] == source["source_fingerprint"]

    stale = client.patch(
        f"{path}/{source['id']}",
        headers=_headers(owner.id),
        json={"expected_version": 1, "description": "stale"},
    )
    assert stale.status_code == 409

    paused = client.post(
        f"{path}/{source['id']}/pause",
        headers=_headers(owner.id),
        json={"expected_version": 2},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["version"] == 3

    resumed = client.post(
        f"{path}/{source['id']}/resume",
        headers=_headers(owner.id),
        json={"expected_version": 3},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    assert resumed.json()["version"] == 4

    archived = client.post(
        f"{path}/{source['id']}/archive",
        headers=_headers(owner.id),
        json={"expected_version": 4},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["version"] == 5
    assert client.get(path, headers=_headers(owner.id)).json()["total"] == 0
    assert (
        client.get(f"{path}?include_archived=true", headers=_headers(owner.id)).json()["total"]
        == 1
    )
    assert client.get(f"{path}/{source['id']}", headers=_headers(owner.id)).status_code == 404


def test_knowledge_source_rejects_secrets_invalid_urls_and_foreign_files() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-knowledge@example.com",
        slug="other-knowledge-space",
    )
    workspace_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="a" * 64,
        storage_key=f"workspaces/{other_workspace.id}/files/{uuid4()}",
        file_metadata={},
    )
    session.add(workspace_file)
    session.commit()
    path = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"

    for payload in (
        {
            "name": "Insecure",
            "source_type": "url",
            "uri": "http://docs.example.com/guide",
        },
        {
            "name": "Leaked config",
            "source_type": "url",
            "uri": "https://docs.example.com/guide?api_key=secret",
        },
        {
            "name": "Leaked header",
            "source_type": "url",
            "uri": "https://docs.example.com/guide",
            "config": {"headers": {"Authorization": "Bearer secret-value"}},
        },
        {
            "name": "Foreign file",
            "source_type": "workspace_file",
            "workspace_file_id": str(workspace_file.id),
        },
    ):
        response = client.post(path, headers=_headers(owner.id), json=payload)
        assert response.status_code in {400, 422}


def test_knowledge_source_list_does_not_cross_workspace_boundary() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="knowledge-reader@example.com",
        slug="knowledge-reader-space",
    )
    path = f"/api/v1/workspaces/{workspace.id}/knowledge/sources"
    created = client.post(
        path,
        headers=_headers(owner.id),
        json={
            "name": "Private source",
            "source_type": "url",
            "uri": "https://private.example.com",
        },
    )
    assert created.status_code == 201
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/knowledge/sources",
        headers=_headers(other_owner.id),
    )
    assert foreign.status_code == 200
    assert foreign.json()["total"] == 0
    assert (
        client.get(
            f"/api/v1/workspaces/{other_workspace.id}/knowledge/sources/{created.json()['id']}",
            headers=_headers(other_owner.id),
        ).status_code
        == 404
    )
