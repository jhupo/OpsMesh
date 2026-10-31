"""DingTalk application protocol behavior against controlled HTTP and real loopback WS."""

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from websockets.asyncio.server import serve

from opsmesh.framework.adapters import (
    BotConnectionConfiguration,
    BotRef,
    BotWebhookRequest,
    OpsMeshMessage,
    OpsMeshMessageSegment,
    ReplyTarget,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.drivers.websockets import WebSocketDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk import DingTalkAdapter
from opsmesh.infrastructure.bots.dingtalk.configuration import parse_configuration
from opsmesh.infrastructure.bots.dingtalk.event import decode_event


def account(**changes):
    return BotConnectionConfiguration(
        **{
            "id": "support",
            "adapter": "dingtalk",
            "self_id": "ding-robot",
            "credentials": {"client_secret": "env:DINGTALK_SECRET"},
            **changes,
        }
    )


def target(channel="group:conversation=="):
    return ReplyTarget(bot=BotRef(adapter_id="dingtalk", bot_id="support"), channel_id=channel)


def callback(**changes):
    return {
        "robotCode": "ding-robot",
        "senderStaffId": "user-001",
        "senderId": "openid-different-from-staff-id",
        "msgId": "message-001",
        "createAt": 1730000000000,
        "conversationType": "2",
        "conversationId": "conversation==",
        "isInAtList": True,
        "msgtype": "text",
        "text": {"content": "ops status"},
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=SENSITIVE",
        **changes,
    }


@asynccontextmanager
async def adapter_with(handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DingTalkAdapter(
            OpsMeshDriver(), client, lambda ref: "PRIVATE_SECRET", autostart=False
        )
        try:
            yield adapter
        finally:
            await adapter.close()


async def test_text_markdown_private_group_token_cache_and_rotation():
    requests = []
    token_count = 0

    async def handler(request):
        nonlocal token_count
        payload = json.loads(request.content)
        requests.append((request.url.path, payload, dict(request.headers)))
        if request.url.path.endswith("accessToken"):
            token_count += 1
            assert payload == {"appKey": "ding-robot", "appSecret": "PRIVATE_SECRET"}
            return httpx.Response(
                200, json={"accessToken": f"TOKEN-{token_count}", "expireIn": 7200}
            )
        return httpx.Response(200, json={"processQueryKey": f"receipt-{len(requests)}"})

    async with adapter_with(handler) as adapter:
        bot = adapter.get_bot(account())
        await asyncio.gather(*(bot.send(target(), OpsMeshMessage("hello")) for _ in range(5)))
        markdown = OpsMeshMessage(
            OpsMeshMessageSegment(type="markdown", data={"title": "Report", "text": "**done**"})
        )
        result = await bot.send(target("private:user-001"), markdown)
        assert result.startswith("receipt-") and token_count == 1
        path, payload, headers = requests[-1]
        assert path == "/v1.0/robot/oToMessages/batchSend"
        assert payload["userIds"] == ["user-001"] and payload["msgKey"] == "sampleMarkdown"
        assert json.loads(payload["msgParam"]) == {"title": "Report", "text": "**done**"}
        assert headers["x-acs-dingtalk-access-token"] == "TOKEN-1"
        assert requests[1][1]["openConversationId"] == "conversation=="
        assert requests[1][1]["msgKey"] == "sampleText"
        bot.api_client._expires_at = 0
        await bot.send(target(), OpsMeshMessage("expired"))
        assert token_count == 2
        await adapter.disconnect_account("support")
        replacement = adapter.get_bot(account(revision=2))
        assert replacement is not bot and not bot.api_client._token
        await replacement.send(target(), OpsMeshMessage("new configuration"))
        assert token_count == 3


@pytest.mark.parametrize(
    "failure, expected, retryable",
    [
        ("timeout", "DELIVERY_UNKNOWN", False),
        ("connect", "BOT_UNAVAILABLE", True),
        (429, "BOT_RATE_LIMITED", True),
        (401, "BOT_AUTHENTICATION_FAILED", False),
        (503, "DELIVERY_UNKNOWN", False),
    ],
)
async def test_send_outcome_classification_never_blindly_replays(failure, expected, retryable):
    deliveries = 0

    def handler(request):
        nonlocal deliveries
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "TOKEN", "expireIn": 7200})
        deliveries += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_SECRET", request=request)
        if failure == "connect":
            raise httpx.ConnectError("PRIVATE_SECRET", request=request)
        return httpx.Response(failure, json={"message": "PRIVATE_SECRET"})

    async with adapter_with(handler) as adapter:
        with pytest.raises(OpsMeshError) as caught:
            await adapter.get_bot(account()).send(target(), OpsMeshMessage("status"))
        assert (caught.value.code, caught.value.retryable) == (expected, retryable)
        assert "PRIVATE_SECRET" not in caught.value.message and deliveries == 1


async def test_invalid_recipients_and_missing_receipts_are_not_successful_sends():
    responses = [{"processQueryKey": "receipt", "invalidUserIds": ["user-001"]}, {}]

    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "TOKEN", "expireIn": 7200})
        return httpx.Response(200, json=responses.pop(0))

    async with adapter_with(handler) as adapter:
        for code in ["BOT_REQUEST_REJECTED", "DELIVERY_UNKNOWN"]:
            with pytest.raises(OpsMeshError) as caught:
                await adapter.get_bot(account()).send(
                    target("private:user-001"), OpsMeshMessage("hello")
                )
            assert caught.value.code == code


async def test_configuration_is_validated_without_resolving_secret_and_unsupported_paths_fail():
    resolved = []
    async with httpx.AsyncClient() as client:
        adapter = DingTalkAdapter(
            OpsMeshDriver(), client, lambda ref: resolved.append(ref) or "SECRET", autostart=False
        )
        adapter.validate_configuration(account())
        assert not resolved
        for settings in [
            {"transport": "http"},
            {"api_base_url": "https://attacker.invalid"},
            {"api_base_url": "https://api.dingtalk.com?token=SECRET"},
        ]:
            with pytest.raises(OpsMeshError, match="Invalid DingTalk"):
                adapter.validate_configuration(account(settings=settings))
        for credentials in [{}, {"client_secret": "env:SECRET", "surprise": "env:OTHER"}]:
            with pytest.raises(OpsMeshError):
                adapter.validate_configuration(account(credentials=credentials))
        bot = adapter.get_bot(account())
        for message in [
            OpsMeshMessage(OpsMeshMessageSegment.image("resource:image")),
            OpsMeshMessage(),
            OpsMeshMessage(OpsMeshMessageSegment(type="text", data={"text": {"wrong": "type"}})),
        ]:
            with pytest.raises(OpsMeshError) as caught:
                await bot.send(target(), message)
            assert caught.value.code == "UNSUPPORTED_FEATURE"
        with pytest.raises(OpsMeshError, match="does not accept HTTP"):
            await adapter.receive_http(account(), BotWebhookRequest(body=b"{}"))
        assert not resolved


def test_callbacks_use_staff_identity_stable_event_id_and_strip_sensitive_fields():
    configuration = account()
    settings = parse_configuration(configuration)
    incoming = decode_event(callback(), configuration, settings)
    retried = decode_event(callback(sessionWebhook="CHANGED_SECRET"), configuration, settings)
    assert incoming == retried
    assert incoming.event.user_id == "user-001"
    assert incoming.event.channel_id == "group:conversation=="
    assert incoming.event.id == "dingtalk:support:message-001"
    assert incoming.event.get_plaintext() == "ops status"
    assert "SENSITIVE" not in json.dumps(incoming.event.model_dump(mode="json"))
    private = decode_event(callback(conversationType="1"), configuration, settings)
    assert private.event.channel_id == "private:user-001" and private.event.to_me
    markdown = decode_event(
        callback(msgtype="markdown", markdown={"text": "**status**"}), configuration, settings
    )
    assert markdown.event.get_plaintext() == "**status**"
    with pytest.raises(OpsMeshError, match="another robot"):
        decode_event(callback(robotCode="another"), configuration, settings)
    with pytest.raises(OpsMeshError):
        decode_event(callback(msgtype="picture"), configuration, settings)


async def test_real_stream_ack_keepalive_while_callback_blocked_reconnect_and_shutdown():
    processing = asyncio.Event()
    release = asyncio.Event()
    reconnected = asyncio.Event()
    completed = asyncio.Event()
    received, tickets, server_errors = [], [], []
    connections = 0

    def frame(kind, topic, identifier, data):
        return json.dumps(
            {
                "specVersion": "1.0",
                "type": kind,
                "headers": {"topic": topic, "messageId": identifier},
                "data": json.dumps(data),
            }
        )

    async def peer(socket):
        nonlocal connections
        connections += 1
        try:
            query = parse_qs(urlsplit(socket.request.path).query)
            assert query["ticket"] == [f"TICKET-{connections}"]
            if connections == 1:
                await socket.send(
                    frame("CALLBACK", "/v1.0/im/bot/messages/get", "callback-001", callback())
                )
                await asyncio.wait_for(processing.wait(), 3)
                await socket.send(frame("SYSTEM", "ping", "ping-001", {"opaque": "nonce"}))
                ping = json.loads(await asyncio.wait_for(socket.recv(), 3))
                assert ping["headers"]["messageId"] == "ping-001" and json.loads(ping["data"]) == {
                    "opaque": "nonce"
                }
                release.set()
                ack = json.loads(await asyncio.wait_for(socket.recv(), 3))
                assert ack["code"] == 200 and ack["headers"]["messageId"] == "callback-001"
                await socket.send(
                    frame("SYSTEM", "disconnect", "disconnect-001", {"reason": "rotate"})
                )
            else:
                await socket.send(frame("SYSTEM", "ping", "second-ping", {"opaque": "ready"}))
                ready = json.loads(await asyncio.wait_for(socket.recv(), 3))
                assert ready["headers"]["messageId"] == "second-ping"
                reconnected.set()
                await socket.wait_closed()
                completed.set()
        except Exception as error:
            server_errors.append(error)

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        def http_peer(request):
            assert request.url.path == "/v1.0/gateway/connections/open"
            body = json.loads(request.content)
            assert body["clientId"] == "ding-robot" and body["clientSecret"] == "SECRET"
            assert body["subscriptions"] == [
                {"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}
            ]
            ticket = f"TICKET-{len(tickets) + 1}"
            tickets.append(ticket)
            return httpx.Response(
                200, json={"endpoint": f"ws://127.0.0.1:{port}/stream", "ticket": ticket}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(http_peer)) as client:
            driver = WebSocketDriver()
            adapter = DingTalkAdapter(driver, client, lambda ref: "SECRET")

            async def consume(configuration, incoming):
                received.append((configuration, incoming))
                processing.set()
                await release.wait()

            adapter.event_consumer = consume
            configuration = account(settings={"reconnect_seconds": 0.05})
            async with driver.lifespan():
                adapter.configure(configuration)
                await asyncio.wait_for(reconnected.wait(), 5)
                assert adapter.connection_status(configuration).state == "connected"
                assert adapter.bots["support"].ref.bot_id == "support"
                await adapter.disconnect_account("support")
                await asyncio.wait_for(completed.wait(), 3)
                assert not adapter.tasks and not adapter.sessions and not adapter.bots
                await adapter.close()
            assert len(received) == 1 and len(tickets) == 2 and not server_errors
