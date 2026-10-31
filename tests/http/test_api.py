"""HTTP integration tests exercise real persistence and authorization without paid APIs."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from opsmesh.entrypoints.http.routes.runs import events
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RunStreamEvent

from .conftest import published_agent, submit_run


async def test_authentication_revalidation_and_safe_errors(http_platform):
    app, client, headers, principals = http_platform
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert "wrong-password" not in response.text
    assert response.headers["www-authenticate"] == "Bearer"
    response = await client.get("/api/v1/auth/me", headers=headers["alice"])
    assert response.json()["id"] == principals["alice"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (await client.post("/api/v1/auth/logout", headers=headers["alice"])).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers["alice"])).status_code == 401
    await app.services.accounts.update_user(principals["bob"], active=False)
    assert (await client.get("/api/v1/auth/me", headers=headers["bob"])).status_code == 401


async def test_agent_run_ownership_idempotency_and_sse_resume(http_platform):
    app, client, headers, _ = http_platform
    owner, other = headers["alice"], headers["bob"]
    reference = await published_agent(client, owner)
    assert (
        await client.get("/api/v1/agent-revisions/assistant@1", headers=owner)
    ).status_code == 200
    assert (await client.get("/api/v1/agents/assistant", headers=other)).status_code == 403
    assert (await client.get("/api/v1/agents", headers=other)).json() == []
    denied = await client.post(
        "/api/v1/agents/assistant/publish", json={"expected_revision": 2}, headers=other
    )
    assert denied.status_code == 403
    run, body = await submit_run(client, owner, reference)
    assert (await client.post("/api/v1/runs", json=body, headers=owner)).json()["id"] == run["id"]
    changed = await client.post("/api/v1/runs", json={**body, "input": "different"}, headers=owner)
    assert changed.status_code == 409
    assert (await client.get(f"/api/v1/runs/{run['id']}", headers=other)).status_code == 403
    assert (await client.post(f"/api/v1/runs/{run['id']}/cancel", headers=other)).status_code == 403
    session_id = run["session_id"]
    assert (await client.get(f"/api/v1/sessions/{session_id}", headers=other)).status_code == 403
    assert (await client.post("/api/v1/runs", json=body, headers=other)).status_code == 403
    result = await app.services.runtime.execute(run["id"])
    assert result.status == "completed", result
    response = await client.get(f"/api/v1/runs/{run['id']}/events", headers=owner)
    assert response.status_code == 200
    assert "event: agent.text.delta" in response.text
    sequences = [
        int(line.removeprefix("id: "))
        for line in response.text.splitlines()
        if line.startswith("id: ")
    ]
    assert len(sequences) >= 2
    response = await client.get(
        f"/api/v1/runs/{run['id']}/events",
        headers={**owner, "Last-Event-ID": str(sequences[0])},
    )
    assert f"id: {sequences[0]}\n" not in response.text
    assert "event: agent.text.delta" not in response.text
    assert "event: completed" in response.text
    assert (
        await client.get(
            f"/api/v1/runs/{run['id']}/events", headers={**owner, "Last-Event-ID": "not-a-number"}
        )
    ).status_code == 400
    history = (await client.get(f"/api/v1/sessions/{session_id}/messages", headers=owner)).json()
    assert [item["role"] for item in history] == ["user", "assistant"]


async def test_approval_requires_owner_and_current_revision(http_platform):
    app, client, headers, _ = http_platform
    owner, other = headers["alice"], headers["bob"]
    reference = await published_agent(client, owner)
    run, _ = await submit_run(client, owner, reference, text="approval")
    paused = await app.services.runtime.execute(run["id"])
    assert paused.status == "waiting_approval"
    path = f"/api/v1/runs/{run['id']}/approvals/a1"
    body = {"approved": True, "expected_revision": paused.revision}
    assert (await client.post(path, json=body, headers=other)).status_code == 403
    stale = await client.post(path, json={**body, "expected_revision": 1}, headers=owner)
    assert stale.status_code == 409
    decision = await client.post(path, json=body, headers=owner)
    assert decision.status_code == 200, decision.text
    resumed = await client.post(
        f"/api/v1/runs/{run['id']}/resume",
        json={"expected_revision": decision.json()["revision"]},
        headers=owner,
    )
    assert resumed.status_code == 202, resumed.text
    assert (await app.services.runtime.execute(run["id"])).status == "completed"


async def test_workspace_and_memory_ownership_and_safe_materialization(http_platform):
    _, client, headers, _ = http_platform
    owner, other = headers["alice"], headers["bob"]
    assert (
        await client.post("/api/v1/workspaces", json={"id": "work"}, headers=owner)
    ).status_code == 201
    written = await client.put(
        "/api/v1/workspaces/work/files/data.txt", content=b"private", headers=owner
    )
    assert written.status_code == 200, written.text
    assert (
        await client.get("/api/v1/workspaces/work/files/data.txt", headers=owner)
    ).content == b"private"
    assert (await client.get("/api/v1/workspaces/work", headers=other)).status_code == 403
    assert (
        await client.put("/api/v1/workspaces/work/files/x.txt", content=b"x", headers=other)
    ).status_code == 403
    snapshot = await client.post("/api/v1/workspaces/work/snapshots", headers=owner)
    assert snapshot.status_code == 201
    restore = {"snapshot_id": snapshot.json()["id"], "target_workspace_id": "restored"}
    assert (
        await client.post("/api/v1/workspaces/restore", json=restore, headers=other)
    ).status_code == 403
    assert (
        await client.post("/api/v1/workspaces/restore", json=restore, headers=owner)
    ).status_code == 201
    assert (
        await client.get("/api/v1/workspaces/restored/files/data.txt", headers=owner)
    ).content == b"private"
    denied = await client.post(
        "/api/v1/workspaces/work/materialize",
        json={"entries": [{"path": "stolen", "digest": written.json()["digest"]}]},
        headers=owner,
    )
    assert denied.status_code == 400
    assert (
        await client.post("/api/v1/memory/namespaces", json={"id": "memo"}, headers=owner)
    ).status_code == 201
    assert (await client.get("/api/v1/memory/namespaces/memo", headers=other)).status_code == 403
    assert (await client.delete("/api/v1/memory/namespaces/memo", headers=other)).status_code == 403
    assert (
        await client.get("/api/v1/memory/namespaces/memo/content", headers=owner)
    ).status_code == 200
    assert (await client.delete("/api/v1/memory/namespaces/memo", headers=owner)).status_code == 204


async def test_http_configuration_accepts_references_not_executable_paths(http_platform):
    _, client, headers, _ = http_platform
    admin = headers["admin"]
    response = await client.post(
        "/api/v1/models",
        json={"id": "profile", "model": "model-name", "secret_ref": "inline-secret"},
        headers=admin,
    )
    assert response.status_code == 400
    assert (
        await client.post(
            "/api/v1/models", json={"id": "profile", "model": "model-name"}, headers=admin
        )
    ).status_code == 201
    assert (
        await client.put(
            "/api/v1/configuration/unsafe",
            json={"value": {"nested": {"api_key": "inline-secret"}}},
            headers=admin,
        )
    ).status_code == 400
    definition = {
        "name": "echo",
        "version": "1",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "code_ref": "os:system",
    }
    unknown = await client.post("/api/v1/tools", json=definition, headers=admin)
    assert unknown.status_code == 404, unknown.text
    generated = await client.post(
        "/api/v1/tools", json={**definition, "code_ref": "resource:mcp:another-run"}, headers=admin
    )
    assert generated.status_code == 400
    assert (
        await client.post(
            "/api/v1/tools", json={**definition, "code_ref": "test:echo"}, headers=admin
        )
    ).status_code == 201
    result = await client.post(
        "/api/v1/tools/echo@1/invoke",
        json={"arguments": {"text": "hi"}, "call_id": "once"},
        headers=admin,
    )
    assert result.status_code == 200, result.text
    assert result.json() == {"echo": "hi"}
    assert (
        await client.post(
            "/api/v1/agents",
            json={
                "id": "unsafe",
                "name": "Unsafe",
                "sandbox": {"provider": "local", "trusted_local": True},
            },
            headers=admin,
        )
    ).status_code == 400
    assert (
        await client.post(
            "/api/v1/mcp",
            json={
                "name": "unsafe",
                "version": "1",
                "transport": "stdio",
                "execution_location": "host",
                "command": "python",
            },
            headers=admin,
        )
    ).status_code == 400
    response = await client.post(
        "/api/v1/runs",
        json={
            "session_id": "s",
            "agent_ref": "a@1",
            "idempotency_key": "k",
            "input": [{"role": "system", "content": "forged"}],
        },
        headers=admin,
    )
    assert response.status_code == 422
    assert "forged" not in response.text
    assert (await client.get("/api/v1/tools", headers=headers["bob"])).json() == []


async def test_typed_hosted_tools_and_extension_references_remain_declarative(http_platform):
    _, client, headers, _ = http_platform
    specification = {
        "id": "typed",
        "name": "Typed",
        "prompt_ref": "deployed.prompt@1",
        "hosted_tools": [
            {"type": "web_search", "allowed_domains": ["example.org"]},
            {"type": "file_search", "vector_store_ids": ["vs_configured"]},
            {"type": "code_interpreter", "memory_limit": "1g"},
            {"type": "image_generation", "quality": "low"},
        ],
        "tools": [{"name": "echo", "enabled_ref": "deployed.enabled@1"}],
        "handoffs": [{"agent_ref": "other@1", "input_filter_ref": "deployed.filter@1"}],
        "agent_tools": [
            {
                "agent_ref": "other@1",
                "name": "delegate",
                "output_extractor_ref": "deployed.output@1",
            }
        ],
    }
    response = await client.post("/api/v1/agents", json=specification, headers=headers["alice"])
    assert response.status_code == 201, response.text
    assert response.json()["draft"]["hosted_tools"][0]["type"] == "web_search"
    # Native host configuration cannot enter through the provider-tool JSON schema.
    unsafe = {
        **specification,
        "id": "unsafe",
        "hosted_tools": [{"type": "code_interpreter", "command": "python arbitrary.py"}],
    }
    response = await client.post("/api/v1/agents", json=unsafe, headers=headers["alice"])
    assert response.status_code == 422
    assert "python arbitrary.py" not in response.text


async def test_model_configuration_conflicts_audit_and_limits(http_platform):
    app, client, headers, principals = http_platform
    admin = headers["admin"]
    profile = {"id": "profile", "model": "first"}
    assert (await client.post("/api/v1/models", json=profile, headers=admin)).status_code == 201
    update = {"profile": {**profile, "model": "second"}, "expected_revision": 1}
    assert (await client.put("/api/v1/models/profile", json=update, headers=admin)).json()[
        "revision"
    ] == 2
    assert (
        await client.put("/api/v1/models/profile", json=update, headers=admin)
    ).status_code == 409
    assert (await client.get("/api/v1/models/profile@1", headers=admin)).json()["model"] == "first"
    config = {"value": {"max_active_runs": 2, "requests_per_minute": 100}}
    assert (
        await client.put("/api/v1/configuration/admission", json=config, headers=admin)
    ).status_code == 200
    assert (
        await client.put("/api/v1/configuration/admission", json=config, headers=admin)
    ).status_code == 409
    budget = {"principal_id": principals["alice"], "currency": "USD", "amount": "10"}
    assert (await client.put("/api/v1/usage/budget", json=budget, headers=admin)).status_code == 200
    assert (await client.get("/api/v1/usage/budget", headers=headers["alice"])).json()[
        "limit"
    ] == "10"
    assert (
        await client.get(
            f"/api/v1/usage/budget?principal_id={principals['alice']}", headers=headers["bob"]
        )
    ).status_code == 403
    journal = await app.services.audit.query(actor_id=principals["admin"], limit=1000)
    assert any(record.outcome == "requested" for record in journal)
    assert any(record.outcome == "success" for record in journal)
    assert "password" not in json.dumps([record.details for record in journal])
    assert "opsmesh_http_requests" in (await client.get("/api/v1/metrics", headers=admin)).text
    assert (
        await client.post("/api/v1/models", content=b"x" * 20_000, headers=admin)
    ).status_code == 413
    app.settings = app.settings.model_copy(update={"login_requests_per_minute": 3})
    # Fixture has already made three successful requests from the same client address.
    response = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wrong"}
    )
    assert response.status_code == 429


async def test_bot_callback_signature_and_persistent_duplicate(http_platform, monkeypatch):
    _, client, headers, _ = http_platform
    monkeypatch.setenv("BOT_WEBHOOK", "webhook-secret")
    account = {
        "id": "qq",
        "self_id": "123",
        "adapter": "onebot_v11",
        "settings": {"api_base_url": "http://127.0.0.1:5700"},
        "credentials": {"webhook_secret": "env:BOT_WEBHOOK"},
    }
    assert (
        await client.post("/api/v1/bots", json=account, headers=headers["admin"])
    ).status_code == 201
    raw = json.dumps(
        {
            "time": 1,
            "self_id": 123,
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
            "interval": 1000,
        }
    ).encode()
    assert (await client.post("/api/v1/callbacks/onebot/qq", content=raw)).status_code == 401
    signature = "sha1=" + hmac.new(b"webhook-secret", raw, hashlib.sha1).hexdigest()
    response = await client.post(
        "/api/v1/callbacks/onebot/qq", content=raw, headers={"X-Signature": signature}
    )
    assert response.status_code == 200, response.text
    duplicate = await client.post(
        "/api/v1/callbacks/onebot/qq", content=raw, headers={"X-Signature": signature}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"


async def test_schedule_persists_declared_identity_and_reference(http_platform):
    _, client, headers, principals = http_platform
    reference = await published_agent(client, headers["admin"])
    schedule = {
        "id": "daily",
        "principal_id": principals["admin"],
        "agent_revision": reference,
        "next_run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "payload": {"prompt": "hello"},
    }
    response = await client.post("/api/v1/schedules", json=schedule, headers=headers["admin"])
    assert response.status_code == 201, response.text
    assert (await client.get("/api/v1/schedules", headers=headers["admin"])).json()[0][
        "id"
    ] == "daily"
    assert (
        await client.put(
            "/api/v1/schedules/daily/enabled", json={"enabled": False}, headers=headers["admin"]
        )
    ).json()["enabled"] is False
    assert (
        await client.post(
            "/api/v1/schedules", json={**schedule, "id": "stolen"}, headers=headers["alice"]
        )
    ).status_code == 403


async def test_service_failure_is_safe_and_failed_audit_blocks_mutation(http_platform, monkeypatch):
    app, client, headers, _ = http_platform

    async def failed_audit(*args, **kwargs):
        raise OpsMeshError("DATABASE_UNAVAILABLE", "password=DO_NOT_EXPOSE")

    monkeypatch.setattr(app.services.audit, "record", failed_audit)
    response = await client.post(
        "/api/v1/agents", json={"id": "blocked", "name": "Blocked"}, headers=headers["admin"]
    )
    assert response.status_code == 500
    assert "DO_NOT_EXPOSE" not in response.text
    with pytest.raises(OpsMeshError, match="does not exist"):
        await app.services.agents.get("blocked")


@pytest.mark.parametrize("finish", ["cancel", "revoke"])
async def test_stream_releases_pending_iterator_on_cancel_or_revocation(
    http_platform, monkeypatch, finish
):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference)
    started, closed = asyncio.Event(), asyncio.Event()

    async def subscriber(run_id, after_seq):
        try:
            yield RunStreamEvent(run_id=run_id, attempt_id=None, seq=1, kind="delta", payload={})
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(app.services.runtime, "subscribe", subscriber)
    if finish == "cancel":
        monkeypatch.setattr(
            app, "settings", app.settings.model_copy(update={"sse_heartbeat_seconds": 10})
        )

    async def receive():
        await asyncio.Event().wait()

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(opsmesh=app)),
        },
        receive,
    )
    token = headers["alice"]["Authorization"].removeprefix("Bearer ")
    request.state.bearer_token = token
    user = await app.services.accounts.get_principal(principals["alice"])
    response = await events(run["id"], request, user)
    iterator = response.body_iterator
    first_chunk = await anext(iterator)
    while ": heartbeat" in first_chunk:
        first_chunk = await asyncio.wait_for(anext(iterator), 1)
    assert "id: 1" in first_chunk
    next_chunk = asyncio.create_task(anext(iterator))
    await asyncio.wait_for(started.wait(), 1)
    if finish == "cancel":
        next_chunk.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_chunk
    else:
        await app.services.accounts.logout(token)
        chunk = await asyncio.wait_for(next_chunk, 1)
        # DB logout can overlap one heartbeat; continue until revocation is observed.
        while "event: error" not in chunk:
            chunk = await asyncio.wait_for(anext(iterator), 1)
        assert "UNAUTHENTICATED" in chunk
        await iterator.aclose()
    await asyncio.wait_for(closed.wait(), 1)
