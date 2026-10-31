"""Manage Bot-to-Agent bindings through authenticated platform endpoints."""

import hashlib
import hmac
import json

from opsmesh.modules.accounts.api import AccessRole, PermissionGrant

from .conftest import published_agent


async def test_bot_binding_management_and_core_validation(http_platform, monkeypatch):
    app, client, headers, _ = http_platform
    monkeypatch.setenv("BINDING_BOT_SECRET", "test-binding-secret")
    response = await client.post(
        "/api/v1/bots",
        json={
            "id": "support",
            "self_id": "100",
            "adapter": "onebot_v11",
            "settings": {"api_base_url": "http://onebot.test"},
            "credentials": {"webhook_secret": "env:BINDING_BOT_SECRET"},
        },
        headers=headers["admin"],
    )
    assert response.status_code == 201, response.text
    first = await published_agent(client, headers["admin"], identifier="first")
    second = await published_agent(client, headers["admin"], identifier="second")
    collection = "/api/v1/bots/support/routes"
    item = collection + "/private:200"
    body = {"channel_id": "private:200", "agent_revision": "first"}
    response = await client.put(collection, json=body, headers=headers["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["agent_revision"] == first
    assert (await client.get(item, headers=headers["admin"])).json() == response.json()
    assert (await client.get(collection, headers=headers["admin"])).json() == [response.json()]

    for method, path, options in (
        (client.get, collection, {}),
        (client.get, item, {}),
        (client.put, collection, {"json": body}),
        (client.delete, item, {}),
    ):
        assert (await method(path, headers=headers["alice"], **options)).status_code == 403
    assert (await client.get(collection)).status_code == 401

    await app.services.accounts.create_role(
        AccessRole(
            name="bot-managers",
            grants=(PermissionGrant(action="bots.manage"), PermissionGrant(action="bots.receive")),
        )
    )
    manager = await app.services.accounts.create_user(
        "bot-manager", "manager-password-123", roles=("bot-managers",)
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "bot-manager", "password": "manager-password-123"},
    )
    manager_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    assert (await client.put(collection, json=body, headers=manager_headers)).status_code == 403

    response = await client.put(
        "/api/v1/bots/support/identities",
        json={"external_user_id": "200", "principal_id": manager.id},
        headers=headers["admin"],
    )
    assert response.status_code == 200, response.text
    payload = json.dumps(
        {
            "time": 1,
            "post_type": "message",
            "self_id": 100,
            "message_type": "private",
            "message_id": 1,
            "user_id": 200,
            "message": [{"type": "text", "data": {"text": "hello"}}],
        }
    ).encode()
    signature = "sha1=" + hmac.new(b"test-binding-secret", payload, hashlib.sha1).hexdigest()
    response = await client.post(
        "/api/v1/callbacks/onebot/support", content=payload, headers={"X-Signature": signature}
    )
    assert response.status_code == 403, response.text
    assert await app.services.runtime.list_runs(manager.id) == []

    response = await client.put(
        collection, json={**body, "agent_revision": "unpublished@1"}, headers=headers["admin"]
    )
    assert response.status_code == 404, response.text
    assert (await client.get(item, headers=headers["admin"])).json()["agent_revision"] == first
    response = await client.put(
        collection, json={**body, "channel_id": "private:\n200"}, headers=headers["admin"]
    )
    assert response.status_code == 400, response.text
    response = await client.put(
        collection, json={**body, "agent_revision": second}, headers=headers["admin"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_revision"] == second
    assert len((await client.get(collection, headers=headers["admin"])).json()) == 1

    definition = await app.services.agents.get("first")
    await app.services.agents.set_enabled("first", False, definition.revision)
    response = await client.put(collection, json=body, headers=headers["admin"])
    assert response.status_code == 403, response.text
    assert (await client.get(item, headers=headers["admin"])).json()["agent_revision"] == second

    assert (await client.delete(item, headers=headers["admin"])).status_code == 204
    assert (await client.delete(item, headers=headers["admin"])).status_code == 204
    assert (await client.get(item, headers=headers["admin"])).status_code == 404
    assert (await client.get(collection, headers=headers["admin"])).json() == []
