"""Run overrides cross the real HTTP/auth/persistence boundary without changing publication."""

from opsmesh.framework.agents import AgentBackendCapabilities, AgentSpecification

from .conftest import published_agent


async def test_run_override_workspace_authorization_and_idempotency(http_platform):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["alice"])
    await app.create_workspace(principals["alice"], "owned")
    await app.create_workspace(principals["bob"], "foreign")
    session = await app.services.sessions.open(principals["alice"], agent_ref=reference)
    body = {
        "session_id": session.id,
        "agent_ref": reference,
        "input": "hello",
        "idempotency_key": "overrides",
        "overrides": {"workspace_id": "foreign"},
    }
    denied = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert denied.status_code == 403, denied.text
    body["overrides"] = {"workspace_id": "owned", "model_settings": {"max_tokens": 128}}
    response = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["agent"]["workspace_id"] == "owned"
    assert run["agent"]["model_settings"]["max_tokens"] == 128
    assert run["metadata"]["workspace_id"] == "owned"
    published = await app.services.agents.get_revision(reference)
    assert published.workspace_id is None and "max_tokens" not in published.model_settings
    repeated = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert repeated.status_code == 202 and repeated.json()["id"] == run["id"]
    body["overrides"]["model_settings"]["max_tokens"] = 256
    changed = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert changed.status_code == 409, changed.text
    body["overrides"] = {"model_settings": {"base_url": "https://invalid.example"}}
    invalid = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert invalid.status_code == 422, invalid.text


async def test_snapshot_override_authorizes_source_and_ignores_session_workspace(http_platform):
    app, client, headers, principals = http_platform
    # The registry is already frozen; this test uses the registered instance's advertised feature.
    current = app.services.extensions.backend("http_test")
    current.backend.features = AgentBackendCapabilities(sandbox=True)
    spec = AgentSpecification(
        id="sandbox", name="Sandbox", backend="http_test", sandbox={"provider": "docker"}
    )
    draft = await app.services.agents.create(spec, principals["alice"])
    published = await app.services.agents.publish(spec.id, draft.revision)
    for owner, workspace in (("alice", "source"), ("bob", "other")):
        await app.create_workspace(principals[owner], workspace)
    owned = await app.services.workspaces.snapshot("source")
    foreign = await app.services.workspaces.snapshot("other")
    session = await app.services.sessions.open(
        principals["alice"], agent_ref=published.ref, workspace_id="other"
    )
    body = {
        "session_id": session.id,
        "agent_ref": published.ref,
        "input": "hello",
        "idempotency_key": "source-selection",
        "overrides": {"sandbox": {"snapshot_ref": foreign.id}},
    }
    response = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert response.status_code == 403, response.text
    body["overrides"] = {"sandbox": {"snapshot_ref": owned.id}}
    response = await client.post("/api/v1/runs", headers=headers["alice"], json=body)
    assert response.status_code == 202, response.text
    assert response.json()["metadata"]["workspace_id"] is None
    assert response.json()["agent"]["sandbox"]["snapshot_ref"] == owned.id
