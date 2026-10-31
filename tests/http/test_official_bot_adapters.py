"""Official adapters share platform discovery, authorization, routing and message APIs."""

import json
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opsmesh.modules.accounts.api import AccessRole, PermissionGrant

from .conftest import published_agent


def configuration(adapter):
    return {
        "id": f"support-{adapter}",
        "adapter": adapter,
        "self_id": "qq-self-id" if adapter == "qq" else "ding-app-id",
        "settings": {"app_id": "123456"} if adapter == "qq" else {},
        "credentials": {"client_secret": f"env:HTTP_{adapter.upper()}_SECRET"},
    }


def signed_qq_event(payload, secret="official-qq-test-secret"):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    material = secret.encode()
    seed = (material * ((32 + len(material) - 1) // len(material)))[:32]
    signer = Ed25519PrivateKey.from_private_bytes(seed)
    return body, {
        "Content-Type": "application/json",
        "X-Bot-Appid": "123456",
        "X-Signature-Timestamp": timestamp,
        "X-Signature-Ed25519": signer.sign(timestamp.encode() + body).hex(),
    }


async def test_adapter_discovery_and_official_accounts_use_adapter_schemas(
    http_platform, monkeypatch
):
    application, client, headers, _ = http_platform
    monkeypatch.setenv("HTTP_QQ_SECRET", "official-qq-test-secret")
    monkeypatch.setenv("HTTP_DINGTALK_SECRET", "official-dingtalk-test-secret")
    assert (await client.get("/api/v1/bot-adapters")).status_code == 401
    assert (await client.get("/api/v1/bot-adapters", headers=headers["alice"])).status_code == 403
    response = await client.get("/api/v1/bot-adapters", headers=headers["admin"])
    assert response.status_code == 200, response.text
    descriptors = {item["name"]: item for item in response.json()}
    assert {"qq", "dingtalk"} <= descriptors.keys()
    assert "app_id" in descriptors["qq"]["settings_schema"]["properties"]
    assert "stream" in descriptors["dingtalk"]["capabilities"]
    assert descriptors["dingtalk"]["credential_names"] == ["client_secret"]
    for name in ("qq", "dingtalk"):
        created = await client.post(
            "/api/v1/bots", json=configuration(name), headers=headers["admin"]
        )
        assert created.status_code == 201, created.text
        assert created.json()["adapter"] == name
        assert created.json()["credentials"] == configuration(name)["credentials"]
        assert "test-secret" not in created.text
    invalid = await client.post(
        "/api/v1/bots",
        json={**configuration("qq"), "id": "invalid-qq", "settings": {"app_id": "not-a-number"}},
        headers=headers["admin"],
    )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["error"]["code"] == "CONFIG_INVALID"
    assert not application.services.bots.adapters.get("dingtalk").tasks


async def test_http_message_sends_select_real_adapter_and_preserve_qq_reply_identity(
    http_platform, monkeypatch
):
    application, client, headers, principals = http_platform
    monkeypatch.setenv("HTTP_QQ_SECRET", "official-qq-test-secret")
    monkeypatch.setenv("HTTP_DINGTALK_SECRET", "official-dingtalk-test-secret")
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append((request.url.host, request.url.path, body, dict(request.headers)))
        if request.url.path.endswith("getAppAccessToken"):
            assert body == {"appId": "123456", "clientSecret": "official-qq-test-secret"}
            return httpx.Response(200, json={"access_token": "QQ_TOKEN", "expires_in": "7200"})
        if request.url.path.endswith("accessToken"):
            assert body == {"appKey": "ding-app-id", "appSecret": "official-dingtalk-test-secret"}
            return httpx.Response(200, json={"accessToken": "DING_TOKEN", "expireIn": 7200})
        if request.url.host == "api.bot.qq.com":
            assert request.headers["Authorization"] == "QQBot QQ_TOKEN"
            return httpx.Response(200, json={"id": "qq-receipt"})
        assert request.url.host == "api.dingtalk.com"
        assert request.headers["x-acs-dingtalk-access-token"] == "DING_TOKEN"
        return httpx.Response(200, json={"processQueryKey": "dingtalk-receipt"})

    await application.services.accounts.create_role(
        AccessRole(name="official_bot_sender", grants=(PermissionGrant(action="bots.send"),))
    )
    await application.services.accounts.update_user(
        principals["alice"], roles=("authors", "official_bot_sender")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
        for name in ("qq", "dingtalk"):
            monkeypatch.setattr(
                application.services.bots.adapters.get(name), "client", provider_client
            )
            created = await client.post(
                "/api/v1/bots", json=configuration(name), headers=headers["admin"]
            )
            assert created.status_code == 201, created.text
        qq_body = {
            "channel_id": "group:group-openid",
            "message": "QQ reply",
            "message_id": "incoming-id",
            "delivery_sequence": 2,
            "idempotency_key": "qq-reply-1",
        }
        dingtalk_body = {
            "channel_id": "private:staff-001",
            "message": "DingTalk reply",
            "idempotency_key": "ding-reply-1",
        }
        for name, body, receipt in [
            ("qq", qq_body, "qq-receipt"),
            ("dingtalk", dingtalk_body, "dingtalk-receipt"),
        ]:
            response = await client.post(
                f"/api/v1/bots/support-{name}/messages", json=body, headers=headers["alice"]
            )
            assert response.status_code == 200 and response.json()["message_id"] == receipt, (
                response.text
            )
            duplicate = await client.post(
                f"/api/v1/bots/support-{name}/messages", json=body, headers=headers["alice"]
            )
            assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
    sends = [
        entry for entry in requests if not entry[1].endswith(("accessToken", "getAppAccessToken"))
    ]
    assert len(sends) == 2
    assert sends[0][1] == "/v2/groups/group-openid/messages"
    assert sends[0][2]["msg_id"] == "incoming-id" and sends[0][2]["msg_seq"] == 2
    assert sends[1][1] == "/v1.0/robot/oToMessages/batchSend"
    assert sends[1][2]["userIds"] == ["staff-001"]


async def test_signed_qq_webhook_enters_shared_identity_agent_route_and_dedup(
    http_platform, monkeypatch
):
    application, client, headers, principals = http_platform
    monkeypatch.setenv("HTTP_QQ_SECRET", "official-qq-test-secret")
    created = await client.post("/api/v1/bots", json=configuration("qq"), headers=headers["admin"])
    assert created.status_code == 201, created.text
    agent_ref = await published_agent(client, headers["admin"], identifier="qq-assistant")
    route = await client.put(
        "/api/v1/bots/support-qq/routes",
        json={"channel_id": "group:group-openid", "agent_revision": agent_ref},
        headers=headers["admin"],
    )
    assert route.status_code == 200, route.text
    payload = {
        "op": 0,
        "id": "dispatch-event-1",
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "incoming-id",
            "author": {"member_openid": "member-openid"},
            "group_openid": "group-openid",
            "timestamp": "2026-10-31T12:00:00Z",
            "content": "hello from QQ",
        },
    }
    body, signature = signed_qq_event(payload)
    denied = await client.post("/api/v1/callbacks/bots/support-qq", content=body, headers=signature)
    assert denied.status_code == 403, denied.text
    assert not await application.services.runtime.list_runs()
    identity = await client.put(
        "/api/v1/bots/support-qq/identities",
        json={"external_user_id": "member-openid", "principal_id": principals["admin"]},
        headers=headers["admin"],
    )
    assert identity.status_code == 200, identity.text
    accepted = await client.post(
        "/api/v1/callbacks/bots/support-qq", content=body, headers=signature
    )
    assert accepted.status_code == 200 and accepted.json() == {"op": 12}, accepted.text
    duplicate = await client.post(
        "/api/v1/callbacks/bots/support-qq", content=body, headers=signature
    )
    assert duplicate.status_code == 200 and duplicate.json() == {"op": 12}
    runs = await application.services.runtime.list_runs()
    assert len(runs) == 1 and runs[0].principal_id == principals["admin"]
    assert await application.services.runtime.get_run_input(runs[0].id) == "hello from QQ"
    assert runs[0].metadata["reply_target"]["bot"] == {"adapter_id": "qq", "bot_id": "support-qq"}
    assert runs[0].metadata["reply_target"]["message_id"] == "incoming-id"
    altered = body.replace(b"hello from QQ", b"forged input")
    rejected = await client.post(
        "/api/v1/callbacks/bots/support-qq", content=altered, headers=signature
    )
    assert rejected.status_code == 401, rejected.text
    assert len(await application.services.runtime.list_runs()) == 1
