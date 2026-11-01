"""Real local platform services, with a deterministic agent backend and no network calls."""

import pytest
from httpx import ASGITransport, AsyncClient

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.http.app import create_http_app
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentExecutionEvent,
    AgentRunCompleted,
    AgentRunInterrupted,
    ModelUsageObservation,
    PendingToolApproval,
)
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant


class HTTPTestBackend:
    id = "http_test"
    version = "1"
    features = AgentBackendCapabilities(streaming=True, approvals=True, resume=True)

    async def run(self, spec, input, context, *, checkpoint=None, decisions=None, emit=None):
        if input == "approval" and checkpoint is None:
            return AgentRunInterrupted(
                pending=(PendingToolApproval(id="a1", tool_name="send", agent_name="Test"),),
                checkpoint={"waiting": True},
            )
        if checkpoint:
            assert decisions == {"a1": True}
        if emit:
            await emit(AgentExecutionEvent(kind="agent.text.delta", payload={"delta": "hello"}))
        return AgentRunCompleted(output="hello", usage=ModelUsageObservation(requests=1))


@pytest.fixture
async def http_platform(tmp_path):
    application = OpsMeshApplication(
        OpsMeshSettings(
            data_directory=tmp_path,
            worker_enabled=False,
            login_requests_per_minute=1000,
            sse_heartbeat_seconds=0.01,
            max_http_body_bytes=16_384,
        )
    )
    backend = HTTPTestBackend()
    application.services.extensions.register_backend("http_test", lambda: backend)

    async def echo(arguments):
        return {"echo": arguments["text"]}

    application.services.code_tools.register("test:echo", echo)
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
        AsyncClient(
            transport=ASGITransport(app=api, raise_app_exceptions=False),
            base_url="http://opsmesh.test",
        ) as client,
    ):
        headers = {}
        principals = {}
        for username in ("admin", "alice", "bob"):
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": f"{username}-password-123"},
            )
            assert response.status_code == 200, response.text
            headers[username] = {"Authorization": f"Bearer {response.json()['token']}"}
            principals[username] = response.json()["principal"]["id"]
        yield application, client, headers, principals


async def published_agent(client, headers, *, identifier="assistant", backend="http_test"):
    response = await client.post(
        "/api/v1/agents",
        json={"id": identifier, "name": "Assistant", "backend": backend},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/v1/agents/{identifier}/publish",
        json={"expected_revision": response.json()["revision"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return f"{identifier}@{response.json()['revision']}"


async def submit_run(client, headers, agent_ref, *, text="hello", key="k1"):
    response = await client.post("/api/v1/sessions", json={"agent_ref": agent_ref}, headers=headers)
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    body = {
        "session_id": session_id,
        "agent_ref": agent_ref,
        "input": text,
        "idempotency_key": key,
    }
    response = await client.post("/api/v1/runs", json=body, headers=headers)
    assert response.status_code == 202, response.text
    return response.json(), body
