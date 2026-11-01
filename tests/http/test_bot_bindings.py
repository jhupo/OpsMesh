"""Manage Bot-to-Agent bindings through authenticated platform endpoints."""

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
            "adapter": "qq",
            "settings": {"app_id": "12345", "api_base_url": "http://127.0.0.1:5700"},
            "credentials": {"client_secret": "env:BINDING_BOT_SECRET"},
        },
        headers=headers["admin"],
    )
    assert response.status_code == 201, response.text
    first = await published_agent(client, headers["admin"], identifier="first")
    second = await published_agent(client, headers["admin"], identifier="second")
    collection = "/api/v1/bots/support/routes"
    item = collection + "/c2c:200"
    body = {"channel_id": "c2c:200", "agent_revision": "first"}
    response = await client.put(collection, json=body, headers=headers["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["agent_revision"] == first
    assert response.json()["dispatch_mode"] == "direct"
    assert (await client.get(item, headers=headers["admin"])).json() == response.json()
    assert (await client.get(collection, headers=headers["admin"])).json() == [response.json()]

    response = await client.put(
        collection, json={**body, "dispatch_mode": "managed"}, headers=headers["admin"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["dispatch_mode"] == "managed"
    assert (await client.get(item, headers=headers["admin"])).json()["dispatch_mode"] == "managed"
    response = await client.put(
        collection, json={**body, "dispatch_mode": "unsupported"}, headers=headers["admin"]
    )
    assert response.status_code == 422, response.text
    assert (await client.put(collection, json=body, headers=headers["admin"])).status_code == 200

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
    from tests.bots.fixtures import webhook

    payload, signature = webhook(secret_value="test-binding-secret")
    response = await client.post(
        "/api/v1/callbacks/bots/support", content=payload, headers=signature
    )
    assert response.status_code == 403, response.text
    assert await app.services.runtime.list_runs(manager.id) == []

    response = await client.put(
        collection, json={**body, "agent_revision": "unpublished@1"}, headers=headers["admin"]
    )
    assert response.status_code == 404, response.text
    assert (await client.get(item, headers=headers["admin"])).json()["agent_revision"] == first
    response = await client.put(
        collection, json={**body, "channel_id": "c2c:\n200"}, headers=headers["admin"]
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
