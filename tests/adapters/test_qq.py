"""Official QQ signing vectors and controlled HTTP peers; no external Bot messages."""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from opsmesh.framework.adapters import (
    BotConnectionConfiguration,
    BotRef,
    BotWebhookRequest,
    OpsMeshMessage,
    OpsMeshMessageSegment,
    ReplyTarget,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq import QQAdapter, QQBotSettings, QQMessageEvent
from opsmesh.infrastructure.bots.qq.authentication import QQWebhookAuthenticator
from opsmesh.infrastructure.bots.qq.client import QQAPIClient
from opsmesh.infrastructure.bots.qq.event import decode_event

SECRET = "test-qq-client-secret"


def test_default_delivery_sequence_preserves_legacy_target_serialization() -> None:
    original = {
        "bot": {"adapter_id": "qq", "bot_id": "support-qq"},
        "channel_id": "c2c:userABC",
        "thread_id": None,
        "message_id": None,
    }
    default = ReplyTarget.model_validate(original)
    assert default.delivery_sequence == 1 and default.model_dump() == original
    subsequent = ReplyTarget.model_validate({**original, "delivery_sequence": 2})
    assert subsequent.model_dump()["delivery_sequence"] == 2


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.invalid",
        "https://api.bot.qq.com.attacker.invalid",
        "http://api.bot.qq.com",
        "https://api.bot.qq.com:8443",
        "https://api.bot.qq.com?token=secret",
        "https://user@api.bot.qq.com",
    ],
)
def test_api_endpoint_rejects_untrusted_hosts_and_credentials(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        QQBotSettings(app_id="12345", api_base_url=endpoint)


def test_sandbox_retains_shared_token_endpoint_and_loopback_is_explicit() -> None:
    sandbox = QQBotSettings(app_id="12345", api_base_url="https://sandbox.api.sgroup.qq.com")
    assert sandbox.token_endpoint == "https://api.bot.qq.com/app/getAppAccessToken"
    loopback = QQBotSettings(app_id="12345", api_base_url="http://127.0.0.1:8123")
    assert loopback.token_endpoint == "http://127.0.0.1:8123/app/getAppAccessToken"


def configuration(**updates: Any) -> BotConnectionConfiguration:
    return BotConnectionConfiguration.model_validate(
        {
            "id": "support-qq",
            "adapter": "qq",
            "self_id": "90001",
            "settings": {"app_id": "12345"},
            "credentials": {"client_secret": "env:QQ_CLIENT_SECRET"},
            **updates,
        }
    )


def target(channel: str = "group:groupABC", **values: Any) -> ReplyTarget:
    return ReplyTarget(
        bot=BotRef(adapter_id="qq", bot_id="support-qq"), channel_id=channel, **values
    )


def signed_request(payload: dict[str, Any], *, timestamp: str | None = None) -> BotWebhookRequest:
    timestamp = timestamp or str(int(time.time()))
    body = json.dumps(payload, separators=(",", ":")).encode()
    seed = (SECRET.encode() * 2)[:32]
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(timestamp.encode() + body).hex()
    return BotWebhookRequest(
        body=body,
        headers={
            "X-Bot-Appid": "12345",
            "X-Signature-Timestamp": timestamp,
            "X-Signature-Ed25519": signature,
        },
    )


def incoming(kind: str = "GROUP_AT_MESSAGE_CREATE", **data: Any) -> dict[str, Any]:
    return {
        "op": 0,
        "id": "callback-1",
        "s": 17,
        "t": kind,
        "d": {
            "id": "message-1",
            "author": {"member_openid": "memberABC", "user_openid": "userABC", "id": "memberABC"},
            "group_openid": "groupABC",
            "channel_id": "10002",
            "guild_id": "10003",
            "content": "hello",
            "timestamp": "2026-10-31T19:00:00+08:00",
            **data,
        },
    }


def test_official_ed25519_seed_public_key_vector() -> None:
    authenticator = QQWebhookAuthenticator("naOC0ocQE3shWLAfffVLB1rhYPG7", clock=lambda: 1725442341)
    public_key = Ed25519PublicKey.from_public_bytes(
        bytes(
            [
                215,
                195,
                98,
                254,
                120,
                174,
                248,
                31,
                242,
                50,
                135,
                180,
                147,
                98,
                139,
                93,
                176,
                42,
                60,
                79,
                227,
                11,
                33,
                94,
                77,
                25,
                96,
                155,
                93,
                118,
                103,
                58,
            ]
        )
    )
    challenge = authenticator.challenge({"event_ts": "1725442341", "plain_token": "testToken"})
    public_key.verify(bytes.fromhex(challenge["signature"]), b"1725442341testToken")


def test_official_registration_challenge_vector() -> None:
    authenticator = QQWebhookAuthenticator("DG5g3B4j9X2KOErG", clock=lambda: 1725442341)
    response = authenticator.challenge(
        {"plain_token": "Arq0D5A61EgUu4OxUvOp", "event_ts": "1725442341"}
    )
    assert response == {
        "plain_token": "Arq0D5A61EgUu4OxUvOp",
        "signature": (
            "87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01bcf"
            "cadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706"
        ),
    }


@pytest.mark.parametrize("mutation", ["unsigned", "tamper", "expired", "wrong-app"])
async def test_webhook_rejects_untrusted_requests(mutation: str) -> None:
    request = signed_request(incoming(), timestamp="1" if mutation == "expired" else None)
    headers = dict(request.headers)
    if mutation == "unsigned":
        headers.pop("X-Signature-Ed25519")
    if mutation == "wrong-app":
        headers["X-Bot-Appid"] = "other"
    body = request.body + b" " if mutation == "tamper" else request.body
    async with httpx.AsyncClient() as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        with pytest.raises(OpsMeshError) as error:
            await adapter.receive_http(configuration(), BotWebhookRequest(body, headers))
    assert error.value.code == "UNAUTHORIZED"


async def test_callback_registration_is_not_a_general_signing_oracle() -> None:
    async with httpx.AsyncClient() as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        timestamp = str(int(time.time()))
        good = {"op": 13, "d": {"plain_token": "SomeValidToken123", "event_ts": timestamp}}
        result = await adapter.receive_http(
            configuration(),
            BotWebhookRequest(
                json.dumps(good).encode(),
                {"X-Bot-Appid": "12345"},
            ),
        )
        assert result.incoming is None
        assert result.response.body["plain_token"] == "SomeValidToken123"
        good["d"]["plain_token"] = json.dumps(incoming())
        with pytest.raises(OpsMeshError) as error:
            await adapter.receive_http(
                configuration(),
                BotWebhookRequest(
                    json.dumps(good).encode(),
                    {"X-Bot-Appid": "12345"},
                ),
            )
        assert error.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("kind", "channel"),
    [
        ("C2C_MESSAGE_CREATE", "c2c:userABC"),
        ("GROUP_AT_MESSAGE_CREATE", "group:groupABC"),
        ("GROUP_MESSAGE_CREATE", "group:groupABC"),
        ("AT_MESSAGE_CREATE", "channel:10002"),
        ("DIRECT_MESSAGE_CREATE", "dms:10003"),
    ],
)
async def test_authenticated_messages_normalize_into_shared_contract(
    kind: str, channel: str
) -> None:
    payload = incoming(kind, message_scene={"ext": ["msg_idx=original-1", "auth_token=private"]})
    async with httpx.AsyncClient() as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        result = await adapter.receive_http(configuration(), signed_request(payload))
    assert result.response.body == {"op": 12}
    assert result.incoming is not None
    event = result.incoming.event
    assert isinstance(event, QQMessageEvent)
    assert event.channel_id == channel and event.message_id == "message-1"
    assert event.get_plaintext() == "hello"
    assert "private" not in event.model_dump_json()
    assert "private" not in json.dumps(result.incoming.payload)
    assert event.to_me is (kind != "GROUP_MESSAGE_CREATE")


def test_callback_retry_identity_uses_message_index_and_ignores_transport_sequence() -> None:
    first = incoming(message_scene={"ext": ["msg_idx=message-A", "auth_token=first-secret"]})
    second = incoming(message_scene={"ext": ["msg_idx=message-A", "auth_token=rotated-secret"]})
    second.update({"id": "different-envelope", "s": 99})
    decoded = [
        decode_event(item, account_id="support", self_id="90001") for item in (first, second)
    ]
    assert decoded[0].event.id == decoded[1].event.id
    assert decoded[0].payload == decoded[1].payload
    second["d"]["message_scene"]["ext"] = ["msg_idx=message-B"]
    assert (
        decode_event(second, account_id="support", self_id="90001").event.id != decoded[0].event.id
    )


def test_same_conversation_on_distinct_bot_accounts_has_independent_sessions() -> None:
    first = decode_event(incoming(), account_id="support", self_id="90001").event
    second = decode_event(incoming(), account_id="operations", self_id="90002").event
    assert first.get_session_id() == "qq:support:group:groupABC"
    assert second.get_session_id() == "qq:operations:group:groupABC"
    assert first.id != second.id


@pytest.mark.parametrize(
    ("channel", "path", "typed"),
    [
        ("c2c:userABC", "/v2/users/userABC/messages", True),
        ("group:groupABC", "/v2/groups/groupABC/messages", True),
        ("channel:10002", "/channels/10002/messages", False),
        ("dms:10003", "/dms/10003/messages", False),
    ],
)
async def test_send_text_reply_preserves_target_message_and_delivery_sequence(channel, path, typed):
    requests = []

    def peer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/app/getAppAccessToken":
            assert json.loads(request.content) == {"appId": "12345", "clientSecret": SECRET}
            return httpx.Response(200, json={"access_token": "access", "expires_in": "7200"})
        assert request.url.path == path
        assert request.headers["Authorization"] == "QQBot access"
        data = json.loads(request.content)
        assert data["msg_id"] == "inbound-message" and data["content"] == "answer"
        if typed:
            assert data["msg_seq"] == 3 and data["msg_type"] == 0
        else:
            assert "msg_seq" not in data and "msg_type" not in data
        return httpx.Response(200, json={"id": "delivered-message"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        bot = adapter.get_bot(configuration())
        assert (
            await bot.send(
                target(channel, message_id="inbound-message", delivery_sequence=3),
                OpsMeshMessage("answer"),
            )
            == "delivered-message"
        )
    assert len(requests) == 2


async def test_markdown_request_does_not_mix_content_and_token_is_cached() -> None:
    paths = []

    async def peer(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("getAppAccessToken"):
            await asyncio.sleep(0)
            return httpx.Response(200, json={"access_token": "access", "expires_in": 7200})
        data = json.loads(request.content)
        assert data == {"markdown": {"content": "# Hello"}, "msg_type": 2}
        return httpx.Response(200, json={"id": "delivered"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        bot = adapter.get_bot(configuration())
        message = OpsMeshMessage(
            OpsMeshMessageSegment(type="markdown", data={"content": "# Hello"})
        )
        await asyncio.gather(*(bot.send(target(), message) for _ in range(3)))
        assert paths.count("/app/getAppAccessToken") == 1
        await adapter.disconnect_account("support-qq")
        await adapter.get_bot(configuration()).send(target(), message)
        assert paths.count("/app/getAppAccessToken") == 2


async def test_token_refresh_uses_expiry_margin() -> None:
    clock = [100.0]
    tokens = []

    def peer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getAppAccessToken"):
            tokens.append("token")
            return httpx.Response(
                200, json={"access_token": f"access{len(tokens)}", "expires_in": 120}
            )
        return httpx.Response(200, json={"id": "sent"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
        qq = QQAPIClient(
            client, QQBotSettings(app_id="12345"), lambda: SECRET, clock=lambda: clock[0]
        )
        await qq.post("/channels/1/messages", {"content": "first"})
        clock[0] = 159
        await qq.post("/channels/1/messages", {"content": "second"})
        assert len(tokens) == 1
        clock[0] = 161
        await qq.post("/channels/1/messages", {"content": "third"})
        assert len(tokens) == 2


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        ("read_timeout", "DELIVERY_UNKNOWN", False),
        ("connect_timeout", "BOT_UNAVAILABLE", True),
        ("server_error", "DELIVERY_UNKNOWN", False),
        ("rate_limit", "BOT_RATE_LIMITED", True),
        ("rejected", "BOT_REQUEST_REJECTED", False),
        ("missing_receipt", "DELIVERY_UNKNOWN", False),
    ],
)
async def test_delivery_errors_do_not_automatically_resend(failure, code, retryable):
    sends = []

    def peer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "access", "expires_in": 7200})
        sends.append(request)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("after sending", request=request)
        if failure == "connect_timeout":
            raise httpx.ConnectTimeout("before sending", request=request)
        return {
            "server_error": httpx.Response(503),
            "rate_limit": httpx.Response(429),
            "rejected": httpx.Response(
                400, json={"code": 40034105, "message": "private-provider-info"}
            ),
            "missing_receipt": httpx.Response(200, json={}),
        }[failure]

    async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        with pytest.raises(OpsMeshError) as error:
            await adapter.get_bot(configuration()).send(target(), OpsMeshMessage("answer"))
    assert error.value.code == code and error.value.retryable is retryable
    assert "private-provider-info" not in str(error.value)
    assert len(sends) == 1


async def test_invalid_capabilities_and_configuration_fail_before_network() -> None:
    requests = []
    resolved = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: requests.append(request))
    ) as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: resolved.append(ref) or SECRET)
        with pytest.raises(OpsMeshError) as error:
            adapter.validate_configuration(configuration(credentials={}))
        assert error.value.code == "CONFIG_INVALID"
        adapter.validate_configuration(configuration())
        bot = adapter.get_bot(configuration())
        for invalid_target, message in [
            (target("group:../../accounts"), OpsMeshMessage("text")),
            (
                target(),
                OpsMeshMessage(OpsMeshMessageSegment.image("https://example.test/image.png")),
            ),
            (target(), OpsMeshMessage("")),
        ]:
            with pytest.raises(OpsMeshError):
                await bot.send(invalid_target, message)
        with pytest.raises(OpsMeshError) as error:
            await bot.upload_file(target(), "file.txt", b"data")
        assert error.value.code == "UNSUPPORTED_FEATURE"
        assert requests == [] and resolved == []


async def test_disable_and_configuration_rotation_discard_tokens_without_closing_http_client() -> (
    None
):
    async with httpx.AsyncClient() as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: SECRET)
        original = adapter.get_bot(configuration())
        replacement = adapter.get_bot(
            configuration(revision=2, credentials={"client_secret": "env:NEW_SECRET"})
        )
        assert original is not replacement
        adapter.configure(configuration(enabled=False, revision=3))
        assert not adapter.bots
        assert adapter.connection_status(configuration(enabled=False)).state == "disabled"
        await adapter.close()
        assert not client.is_closed
