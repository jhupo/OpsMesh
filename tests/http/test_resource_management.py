"""HTTP and direct protected services share resource governance rules."""

import pytest

from opsmesh.framework.agents import AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.models.api import ModelProfile


async def test_model_and_tool_version_availability_http(http_platform):
    app, client, headers, principals = http_platform
    admin = headers["admin"]
    response = await client.post(
        "/api/v1/models", headers=admin, json={"id": "local", "model": "offline"}
    )
    assert response.status_code == 201, response.text
    response = await client.put(
        "/api/v1/models/local@1/availability",
        headers=admin,
        json={"enabled": False, "expected_revision": 0},
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/models/local@1", headers=admin)).status_code == 200
    with pytest.raises(OpsMeshError, match="disabled"):
        await app.services.models.get("local")
    assert (await client.get("/api/v1/models/local/versions", headers=admin)).json()[0][
        "revision"
    ] == 1
    assert (await client.get("/api/v1/models/local@1/references", headers=admin)).json() == []
    denied = await client.put(
        "/api/v1/models/local@1/availability",
        headers=headers["alice"],
        json={"enabled": True, "expected_revision": 1},
    )
    assert denied.status_code == 403
    with pytest.raises(OpsMeshError):
        await app.services.model_management.set_enabled(
            "local@1", True, actor_id=principals["alice"], expected_revision=1
        )
    response = await client.post(
        "/api/v1/tools",
        headers=admin,
        json={
            "name": "echo",
            "version": "1",
            "code_ref": "test:echo",
            "input_schema": {"type": "object"},
        },
    )
    assert response.status_code == 201, response.text
    response = await client.put(
        "/api/v1/capability-versions/tool/echo@1/availability",
        headers=admin,
        json={"enabled": False, "expected_revision": 0},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/tools/echo@1/invoke",
        headers=admin,
        json={"arguments": {"text": "hello"}, "call_id": "blocked"},
    )
    assert response.status_code == 403, response.text
    assert (await client.get("/api/v1/capability-versions/tool/echo@1", headers=admin)).json()[
        "code_ref"
    ] == "test:echo"


async def test_workspace_files_and_memory_policy_http(http_platform):
    app, client, headers, principals = http_platform
    alice, bob = headers["alice"], headers["bob"]
    assert (
        await client.post("/api/v1/workspaces", headers=alice, json={"id": "files"})
    ).status_code == 201
    assert (
        await client.put("/api/v1/workspaces/files/files/a.txt", headers=alice, content=b"hello")
    ).status_code == 200
    assert [
        entry["id"] for entry in (await client.get("/api/v1/workspaces", headers=alice)).json()
    ] == ["files"]
    assert (await client.get("/api/v1/workspaces", headers=bob)).json() == []
    tree = await client.get("/api/v1/workspaces/files/tree", headers=alice)
    assert tree.status_code == 200 and tree.json()[0]["path"] == "a.txt", tree.text
    assert (
        await client.post(
            "/api/v1/workspaces/files/rename",
            headers=alice,
            json={"source": "a.txt", "target": "b.txt"},
        )
    ).status_code == 200
    assert (
        await client.delete("/api/v1/workspaces/files/files/b.txt", headers=bob)
    ).status_code == 403
    assert (
        await client.delete("/api/v1/workspaces/files/files/b.txt", headers=alice)
    ).status_code == 204
    response = await client.post(
        "/api/v1/memory/namespaces",
        headers=alice,
        json={"id": "notes", "readers": [principals["bob"]]},
    )
    assert response.status_code == 201, response.text
    assert len((await client.get("/api/v1/memory/namespaces", headers=bob)).json()) == 1
    response = await client.put(
        "/api/v1/memory/namespaces/notes/settings",
        headers=alice,
        json={"expected_revision": 1, "settings": {"readers": []}},
    )
    assert response.status_code == 200, response.text
    assert (
        await client.get("/api/v1/memory/namespaces/notes/content", headers=bob)
    ).status_code == 403
    with pytest.raises(OpsMeshError):
        await app.services.workspace_management.read("files", "a.txt", actor_id=principals["bob"])


async def test_knowledge_import_update_rebuild_and_delete_http(http_platform):
    _, client, headers, _ = http_platform
    admin = headers["admin"]
    response = await client.post(
        "/api/v1/knowledge/collections", headers=admin, json={"id": "handbook", "title": "Handbook"}
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/knowledge/collections/handbook/imports",
        headers=admin,
        json={
            "idempotency_key": "batch",
            "sources": [{"id": "intro", "text": "# Guide\nUse the service.", "format": "markdown"}],
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "succeeded" and job["completed"] == 1
    assert (
        await client.get(f"/api/v1/knowledge/jobs/{job['id']}", headers=admin)
    ).status_code == 200
    document_id = job["document_ids"][0]
    response = await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=admin)
    assert response.status_code == 200, response.text
    value = response.json()
    value["document"]["text"] = "Updated instructions"
    response = await client.put(
        f"/api/v1/knowledge/documents/{document_id}",
        headers=admin,
        json={"document": value["document"], "expected_revision": value["revision"]},
    )
    assert response.status_code == 204, response.text
    response = await client.post(
        "/api/v1/knowledge/collections/handbook/rebuild",
        headers=admin,
        json={"idempotency_key": "rebuild"},
    )
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert (
        await client.get("/api/v1/knowledge/collections", headers=headers["alice"])
    ).json() == []
    response = await client.delete(
        "/api/v1/knowledge/collections/handbook?expected_revision=1", headers=admin
    )
    assert response.status_code == 204, response.text
    assert (
        await client.get("/api/v1/knowledge/handbook/search?query=Updated", headers=admin)
    ).status_code == 404


async def test_snapshot_references_and_model_impact_are_visible_and_enforced(http_platform):
    app, client, headers, principals = http_platform
    owner = headers["alice"]
    await app.create_workspace(principals["alice"], "references")
    snapshot = await app.services.workspaces.snapshot("references")
    await app.services.models.create(ModelProfile(id="shared", model="offline"))
    specification = AgentSpecification(
        id="referencing",
        name="References",
        backend="http_test",
        model_profile_ref="shared@1",
        sandbox=SandboxConfiguration(snapshot_ref=snapshot.id),
    )
    definition = await app.services.agents.create(specification, principals["alice"])
    response = await client.get("/api/v1/models/shared@1/references", headers=headers["admin"])
    assert response.status_code == 200, response.text
    assert any(
        item["reference"] == definition.id and item["field"] == "draft.model_profile_ref"
        for item in response.json()
    )
    response = await client.delete(
        f"/api/v1/workspaces/references/snapshots/{snapshot.id}", headers=owner
    )
    assert response.status_code == 409, response.text
    await app.services.agents.update(
        definition.id, specification.model_copy(update={"sandbox": None}), definition.revision
    )
    response = await client.delete(
        f"/api/v1/workspaces/references/snapshots/{snapshot.id}", headers=owner
    )
    assert response.status_code == 204, response.text


async def test_resource_dependency_facades_enforce_actor_and_keep_embedding_extensions(
    http_platform,
):
    from opsmesh.dependencies import (
        get_embedding_registry,
        get_mcp_catalog,
        get_memory,
        get_script_tools,
        get_skills,
    )
    from opsmesh.modules.capabilities.internal.embeddings import LocalHashEmbedding
    from opsmesh.modules.memory.api import MemoryNamespace

    app, _, _, principals = http_platform
    for catalog in (get_skills(), get_script_tools(), get_mcp_catalog()):
        with pytest.raises(OpsMeshError) as denied:
            await catalog.get("private@1", actor_id=principals["alice"])
        assert denied.value.code in {"AUTHORIZATION_DENIED", "FORBIDDEN"}
    with pytest.raises(OpsMeshError, match="restart"):
        get_embedding_registry().register_embedding_provider(LocalHashEmbedding())
    await get_memory().create_namespace(
        MemoryNamespace(id="private", owner=principals["alice"]), actor_id=principals["alice"]
    )
    await app.services.accounts.update_user(principals["alice"], active=False)
    with pytest.raises(OpsMeshError):
        await get_memory().read("private", principal_id=principals["alice"])
