"""HTTP and plugin dependency callers share Bot identity and credential boundaries."""

import pytest

from opsmesh import dependencies
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.bots.api import BotAccount

from .conftest import published_agent


async def configure_operator(application, principals, *, grants=()):
    await application.services.accounts.create_role(
        AccessRole(
            name="http_bot_operator",
            grants=(
                PermissionGrant(action="bots.manage", resource="support"),
                PermissionGrant(action="bots.read", resource="support"),
                *grants,
            ),
        )
    )
    await application.services.accounts.update_user(
        principals["alice"], roles=("authors", "http_bot_operator")
    )


async def test_http_and_depends_cannot_map_bot_manager_to_admin(http_platform, monkeypatch):
    application, client, headers, principals = http_platform
    monkeypatch.setenv("BOT_AUTHORIZATION_WEBHOOK", "local-test-secret")
    config = BotAccount(
        id="support",
        self_id="100",
        adapter="onebot_v11",
        settings={"api_base_url": "http://onebot.test"},
        credentials={"webhook_secret": "env:BOT_AUTHORIZATION_WEBHOOK"},
    )
    await configure_operator(
        application,
        principals,
        grants=(
            PermissionGrant(action="accounts:bot_identity:manage", resource=principals["bob"]),
        ),
    )
    response = await client.post(
        "/api/v1/bots", json=config.model_dump(mode="json"), headers=headers["admin"]
    )
    assert response.status_code == 201, response.text
    response = await client.put(
        "/api/v1/bots/support/identities",
        json={"external_user_id": "200", "principal_id": principals["admin"]},
        headers=headers["alice"],
    )
    assert response.status_code == 403, response.text
    with pytest.raises(OpsMeshError) as denied:
        await dependencies.get_bots().bind_identity(
            "support", "200", principals["admin"], actor_id=principals["alice"]
        )
    assert denied.value.code == "FORBIDDEN"
    response = await client.put(
        "/api/v1/bots/support/identities",
        json={"external_user_id": "200", "principal_id": principals["bob"]},
        headers=headers["alice"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["principal_id"] == principals["bob"]


async def test_http_and_depends_reject_unauthorized_secret_references(http_platform, monkeypatch):
    application, client, headers, principals = http_platform
    monkeypatch.setenv("BOT_AUTHORIZATION_WEBHOOK", "local-test-secret")
    monkeypatch.setenv("BOT_UNRELATED_CREDENTIAL", "synthetic-private-value")
    await configure_operator(
        application,
        principals,
        grants=(PermissionGrant(action="secret:use", resource="env:BOT_AUTHORIZATION_WEBHOOK"),),
    )
    config = BotAccount(
        id="support",
        self_id="100",
        adapter="onebot_v11",
        settings={"api_base_url": "https://attacker.invalid"},
        credentials={
            "webhook_secret": "env:BOT_AUTHORIZATION_WEBHOOK",
            "access_token": "env:BOT_UNRELATED_CREDENTIAL",
        },
    )
    response = await client.post(
        "/api/v1/bots", json=config.model_dump(mode="json"), headers=headers["alice"]
    )
    assert response.status_code == 403, response.text
    with pytest.raises(OpsMeshError) as denied:
        await dependencies.get_bots().create_account(config, actor_id=principals["alice"])
    assert denied.value.code == "FORBIDDEN"
    response = await client.post(
        "/api/v1/bots", json=config.model_dump(mode="json"), headers=headers["admin"]
    )
    assert response.status_code == 201, response.text
    response = await client.put(
        "/api/v1/bots/support",
        json={
            "account": config.model_copy(
                update={"settings": {**config.settings, "api_base_url": "https://another.invalid"}}
            ).model_dump(mode="json"),
            "expected_revision": 1,
        },
        headers=headers["alice"],
    )
    assert response.status_code == 403, response.text
    current = await client.get("/api/v1/bots/support", headers=headers["admin"])
    assert current.json()["revision"] == 1
    assert "synthetic-private-value" not in current.text


async def test_http_and_depends_cannot_reroute_existing_administrator_identity(
    http_platform, monkeypatch
):
    application, client, headers, principals = http_platform
    monkeypatch.setenv("BOT_AUTHORIZATION_WEBHOOK", "local-test-secret")
    await configure_operator(application, principals)
    agent_ref = await published_agent(client, headers["alice"], identifier="controlled")
    config = BotAccount(
        id="support",
        self_id="100",
        adapter="onebot_v11",
        settings={"api_base_url": "http://onebot.test"},
        credentials={"webhook_secret": "env:BOT_AUTHORIZATION_WEBHOOK"},
    )
    await application.services.bots.create_account(config, actor_id=principals["admin"])
    await application.services.bots.bind_identity(
        "support", "200", principals["admin"], actor_id=principals["admin"]
    )
    payload = {"channel_id": "private:200", "agent_revision": agent_ref}
    response = await client.put(
        "/api/v1/bots/support/routes", json=payload, headers=headers["alice"]
    )
    assert response.status_code == 403, response.text
    with pytest.raises(OpsMeshError) as denied:
        await dependencies.get_bots().bind_route(
            "support", "private:200", agent_ref, actor_id=principals["alice"]
        )
    assert denied.value.code == "FORBIDDEN"
    response = await client.put(
        "/api/v1/bots/support/routes", json=payload, headers=headers["admin"]
    )
    assert response.status_code == 200, response.text
    response = await client.delete(
        "/api/v1/bots/support/routes/private:200", headers=headers["alice"]
    )
    assert response.status_code == 403, response.text
    assert (
        await application.services.bots.get_route(
            "support", "private:200", actor_id=principals["admin"]
        )
    ).agent_revision == agent_ref
