"""HTTP composition over real accounts/storage/scripts, with deterministic model/remote peers."""

import base64
import json

import httpx
import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.http.app import create_http_app
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentRunCompleted,
    ModelUsageObservation,
)
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant


class ScriptBackend:
    id = "script_test"
    version = "1"
    features = AgentBackendCapabilities()

    async def run(self, spec, input, context, *, checkpoint=None, decisions=None, emit=None):
        binding = spec.tools[0]
        result = await context.invoke_tool(binding.tool_ref, {"number": 21}, "script-call")
        return AgentRunCompleted(
            output=result, usage=ModelUsageObservation(requests=0, usage_available=False)
        )


class SemanticTestEmbedding:
    id = "http-semantic-test-v1"
    dimensions = 2

    async def embed(self, texts):
        return tuple(
            (1.0, 0.0) if any(term in text for term in ("car", "automobile")) else (0.0, 1.0)
            for text in texts
        )


@pytest.fixture
async def resource_platform(tmp_path):
    application = OpsMeshApplication(
        OpsMeshSettings(
            data_directory=tmp_path,
            worker_enabled=False,
            login_requests_per_minute=1000,
            script_execution_provider="local",
            allow_trusted_local=True,
        )
    )
    application.services.extensions.register_backend("script_test", ScriptBackend)
    application.services.knowledge.register_embedding_provider(
        SemanticTestEmbedding(), default=True
    )
    await application.initialize(admin_username="admin", admin_password="admin-password-123")
    await application.services.accounts.create_role(
        AccessRole(
            name="authors",
            grants=tuple(
                PermissionGrant(action=action)
                for action in ("agent:create", "agent:run", "workspace:create")
            ),
        )
    )
    for username in ("alice", "bob"):
        await application.services.accounts.create_user(
            username, f"{username}-password-123", roles=("authors",)
        )
    api = create_http_app(application)
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api, raise_app_exceptions=False),
            base_url="http://opsmesh.test/api/v1/",
        ) as client,
    ):
        headers, principals = {}, {}
        for username in ("admin", "alice", "bob"):
            response = await client.post(
                "auth/login", json={"username": username, "password": f"{username}-password-123"}
            )
            assert response.status_code == 200, response.text
            headers[username] = {"Authorization": f"Bearer {response.json()['token']}"}
            principals[username] = response.json()["principal"]["id"]
        yield application, client, headers, principals


async def grant(application, principals, users, *permissions):
    name = "role-" + str(len(await application.services.accounts.list_roles()))
    await application.services.accounts.create_role(
        AccessRole(
            name=name,
            grants=tuple(
                PermissionGrant(action=action, resource=resource)
                for action, resource in permissions
            ),
        )
    )
    for username in users:
        principal = await application.services.accounts.get_principal(principals[username])
        await application.services.accounts.update_user(
            principal.id, roles=(*principal.roles, name)
        )


async def workspace_file(
    client, headers, *, workspace="source", path="main.py", content=b"print('{}')"
):
    response = await client.post("workspaces", json={"id": workspace}, headers=headers)
    assert response.status_code == 201, response.text
    response = await client.put(
        f"workspaces/{workspace}/files/{path}", content=content, headers=headers
    )
    assert response.status_code == 200, response.text


async def test_http_script_publication_to_agent_execution_and_owner_denials(resource_platform):
    application, client, headers, principals = resource_platform
    schema = {
        "type": "object",
        "properties": {"number": {"type": "integer"}},
        "required": ["number"],
    }
    script = (
        b"import json,sys\nfrom pathlib import Path\n"
        b"a=json.loads(Path(sys.argv[1]).read_text())\n"
        b"print(json.dumps({'result':a['number']*2}))\n"
    )
    await workspace_file(client, headers["alice"], content=script)
    declaration = {
        "name": "double",
        "version": "1",
        "entrypoint": "main.py",
        "input_schema": schema,
    }
    body = {"workspace_id": "source", "declaration": declaration}
    assert (await client.post("scripts", json=body, headers=headers["alice"])).status_code == 403
    await grant(
        application,
        principals,
        ("alice", "bob"),
        ("capability:manage", "*"),
        ("capability:read", "*"),
        ("tool:invoke", "double@1"),
    )
    assert (await client.post("scripts", json=body, headers=headers["bob"])).status_code == 403
    assert (
        await client.post(
            "scripts", json={**body, "directory": "../outside"}, headers=headers["alice"]
        )
    ).status_code == 400
    published = await client.post("scripts", json=body, headers=headers["alice"])
    assert published.status_code == 201, published.text
    assert (await client.get("scripts/double@1", headers=headers["alice"])).json()[
        "digest"
    ] == published.json()["digest"]
    tool = await client.get("tools/double@1", headers=headers["alice"])
    assert tool.status_code == 200 and tool.json()["code_ref"] == "script:double@1"
    specification = {
        "id": "script-agent",
        "name": "Script",
        "backend": "script_test",
        "tools": [{"name": "double", "tool_ref": "double@1", "input_schema": schema}],
    }
    created = await client.post("agents", json=specification, headers=headers["alice"])
    assert created.status_code == 201, created.text
    published_agent = await client.post(
        "agents/script-agent/publish",
        json={"expected_revision": created.json()["revision"]},
        headers=headers["alice"],
    )
    assert published_agent.status_code == 200, published_agent.text
    session = await client.post(
        "sessions", json={"agent_ref": "script-agent@1"}, headers=headers["alice"]
    )
    response = await client.post(
        "runs",
        json={
            "session_id": session.json()["id"],
            "agent_ref": "script-agent@1",
            "input": "double",
            "idempotency_key": "run-one",
        },
        headers=headers["alice"],
    )
    assert response.status_code == 202, response.text
    completed = await application.services.runtime.execute(response.json()["id"])
    assert completed.status == "completed", completed
    assert completed.output == {"result": 42}
    # Existing tool names cannot be rebound to unpublished script implementations.
    invalid = {
        "name": "missing",
        "version": "1",
        "input_schema": schema,
        "code_ref": "script:missing@1",
    }
    assert (await client.post("tools", json=invalid, headers=headers["admin"])).status_code == 404


async def test_http_git_policy_and_remote_sse_definition(resource_platform, tmp_path):
    _, client, headers, _ = resource_platform
    body = {
        "name": "sample",
        "version": "1",
        "source": {"repository": "https://untrusted.invalid/repo.git", "commit": "a" * 40},
    }
    assert (
        await client.post("skill-imports/git", json=body, headers=headers["alice"])
    ).status_code == 403
    denied = await client.post("skill-imports/git", json=body, headers=headers["admin"])
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "AUTHORIZATION_DENIED"
    body["source"]["repository"] = str(tmp_path)
    assert (
        await client.post("skill-imports/git", json=body, headers=headers["admin"])
    ).status_code == 403
    body["source"]["commit"] = "main"
    assert (
        await client.post("skill-imports/git", json=body, headers=headers["admin"])
    ).status_code == 422
    sse = {
        "name": "legacy",
        "version": "1",
        "transport": "sse",
        "execution_location": "remote",
        "url": "http://127.0.0.1:9/sse",
        "allowed_tools": ["echo"],
    }
    assert (await client.post("mcp", json=sse, headers=headers["alice"])).status_code == 403
    accepted = await client.post("mcp", json=sse, headers=headers["admin"])
    assert accepted.status_code == 201 and accepted.json()["transport"] == "sse"
    assert (await client.get("mcp/legacy@1", headers=headers["admin"])).json()["transport"] == "sse"
    assert (
        await client.post(
            "mcp",
            json={**sse, "name": "credential", "url": "https://user:secret@example.invalid/sse"},
            headers=headers["admin"],
        )
    ).status_code == 400


async def test_http_vector_hybrid_selection_and_collection_permissions(resource_platform):
    application, client, headers, principals = resource_platform
    for identifier, collection, text in (
        ("one", "public", "car"),
        ("two", "public", "boat"),
        ("secret", "private", "car"),
    ):
        response = await client.post(
            "knowledge/documents",
            json={"id": identifier, "collection": collection, "text": text},
            headers=headers["admin"],
        )
        assert response.status_code == 201, response.text
    query = {"query": "automobile", "mode": "vector"}
    assert (
        await client.get("knowledge/public/search", params=query, headers=headers["alice"])
    ).status_code == 403
    await grant(application, principals, ("alice",), ("capability:use", "knowledge:read:public"))
    assert (
        await client.get(
            "knowledge/public/search", params={**query, "mode": "lexical"}, headers=headers["alice"]
        )
    ).json() == []
    hits = await client.get("knowledge/public/search", params=query, headers=headers["alice"])
    assert hits.status_code == 200 and [hit["document"]["id"] for hit in hits.json()] == ["one"]
    assert hits.json()[0]["vector_score"] == 1
    hybrid = await client.get(
        "knowledge/public/search",
        params={"query": "car", "mode": "hybrid"},
        headers=headers["alice"],
    )
    assert hybrid.json()[0]["lexical_score"] > 0 and hybrid.json()[0]["score"] < 1
    assert (
        await client.get("knowledge/private/search", params=query, headers=headers["alice"])
    ).status_code == 403
    assert (
        await client.get(
            "knowledge/public/search", params={**query, "mode": "typo"}, headers=headers["admin"]
        )
    ).status_code == 422


async def test_http_bot_file_ownership_and_websocket_optional_secrets(
    resource_platform, monkeypatch
):
    application, client, headers, principals = resource_platform
    monkeypatch.setenv("HTTP_TEST_BOT_TOKEN", "token")
    monkeypatch.setenv("HTTP_TEST_BOT_SECRET", "secret")
    reverse = {
        "id": "reverse",
        "self_id": "100",
        "adapter": "onebot_v11",
        "settings": {"transport": "reverse_websocket"},
        "credentials": {"access_token": "env:HTTP_TEST_BOT_TOKEN"},
    }
    assert (await client.post("bots", json=reverse, headers=headers["alice"])).status_code == 403
    created = await client.post("bots", json=reverse, headers=headers["admin"])
    assert created.status_code == 201 and "webhook_secret" not in created.json()["credentials"]
    assert (
        await client.post(
            "bots",
            json={**reverse, "id": "invalid", "credentials": {"access_token": None}},
            headers=headers["admin"],
        )
    ).status_code == 422
    active = {
        "id": "active",
        "self_id": "101",
        "adapter": "onebot_v11",
        "settings": {"transport": "websocket", "websocket_url": "ws://127.0.0.1:9"},
    }
    assert (await client.post("bots", json=active, headers=headers["admin"])).status_code == 201
    adapter = application.services.bots.adapters.get("onebot_v11")
    assert not adapter.connections.tasks  # This fixture disables active workers.
    bot = {
        "id": "files",
        "self_id": "102",
        "adapter": "onebot_v11",
        "settings": {"api_base_url": "http://onebot.test", "file_upload": "napcat"},
        "credentials": {"webhook_secret": "env:HTTP_TEST_BOT_SECRET"},
    }
    assert (await client.post("bots", json=bot, headers=headers["admin"])).status_code == 201
    await workspace_file(client, headers["alice"], path="report.txt", content=b"private-report")
    await grant(application, principals, ("alice", "bob"), ("bots.send", "files"))
    body = {
        "workspace_id": "source",
        "path": "report.txt",
        "name": "report.txt",
        "channel_id": "private:200",
        "idempotency_key": "file-one",
    }
    assert (
        await client.post("bots/files/files", json=body, headers=headers["bob"])
    ).status_code == 403
    calls = []

    async def upload(request):
        payload = json.loads(request.content)
        assert base64.b64decode(payload["file"].removeprefix("base64://")) == b"private-report"
        calls.append(payload)
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"file_id": "one"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upload)) as bot_client:
        adapter.client = bot_client
        first = await client.post("bots/files/files", json=body, headers=headers["alice"])
        assert first.status_code == 200 and first.json()["file_id"] == "one", first.text
        duplicate = await client.post("bots/files/files", json=body, headers=headers["alice"])
        assert duplicate.json()["duplicate"] and len(calls) == 1
