"""Check DingTalk Stream admission, event mapping, and replies."""

import json
import time
from contextlib import asynccontextmanager, contextmanager
from types import ModuleType

import anyio
import httpx
import pytest

from opsmesh.config import Config, Env
from opsmesh.dispatch.results import DispatchResult
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels import get_event_channel
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConfigurationError,
    DingTalkConnectionSettings,
    DingTalkProtocolError,
    DingTalkSendError,
    parse_callback,
)
from opsmesh.plugins.channels.messages import Message
from opsmesh.plugins.channels.operations import OperationError
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus

SECRET = "dingtalk-test-secret"


def make_plugin(name="support"):
    return Plugin(
        name,
        ModuleType(name),
        name,
        PluginManager(),
        status=PluginStatus.READY,
    )


@contextmanager
def owned_by(plugin):
    token = registry._current_plugin.set(plugin)
    try:
        yield plugin
    finally:
        registry._current_plugin.reset(token)


def make_adapter() -> DingTalkAdapter:
    return DingTalkAdapter(Driver(Env(), Config()))


def declare(
    adapter,
    plugin,
    name="internal",
    secret_ref="DINGTALK_SECRET",
):
    settings = DingTalkConnectionSettings(
        client_id="client-1", client_secret_ref=secret_ref
    )
    with owned_by(plugin):
        channel = adapter.declare(settings, name=name)
    return channel


@asynccontextmanager
async def running(adapter, channel):
    """Drive prepare, start and cleanup for a directly declared connection."""
    await adapter.driver.startup()
    try:
        await channel.prepare()
        await channel.start()
        yield
    finally:
        await channel.close()
        await adapter.driver.shutdown()


@pytest.fixture(autouse=True)
def connected_stream(monkeypatch):
    """Keep transport tests offline while exercising owned Stream tasks."""
    from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream

    async def receive(stream):
        stream.channel.inbound_task_connected()
        await anyio.sleep_forever()

    monkeypatch.setattr(DingTalkStream, "_receive", receive)


async def open_streams(channel):
    with anyio.fail_after(2):
        while not channel.ready:
            await anyio.sleep(0)


async def use_transport(channel, http):
    await channel.client.close()
    from opsmesh.plugins.channels.dingtalk.client import DingTalkClient

    channel._client = DingTalkClient(channel.settings, SECRET, http=http)


async def callback(adapter, channel, payload):
    """Route an official SDK frame and capture its wire acknowledgement."""
    from dingtalk_stream import ChatbotMessage

    replies = []

    class Socket:
        async def send(self, value):
            replies.append(json.loads(value))

    stream = channel._inbound_task
    stream.websocket = Socket()
    await stream.route_message(
        {
            "type": "CALLBACK",
            "headers": {"topic": ChatbotMessage.TOPIC, "messageId": "frame-1"},
            "data": json.dumps(payload),
        }
    )
    assert replies[0]["headers"]["messageId"] == "frame-1"
    return replies[0]


def direct_payload(
    message_id: str = "msg-1", webhook: str | None = None
) -> dict:
    expires = int(time.time() * 1000) + 3600000
    return {
        "msgtype": "text",
        "text": {"content": "hello"},
        "conversationId": "cid-1",
        "conversationTitle": "Direct",
        "conversationType": "1",
        "senderId": "sender-1",
        "senderNick": "Tester",
        "msgId": message_id,
        "sessionWebhook": webhook
        or "https://oapi.dingtalk.com/robot/sendBySession?session=abc",
        "sessionWebhookExpiredTime": expires,
    }


def test_declaration_requires_an_owning_plugin():
    adapter = make_adapter()

    with pytest.raises(DingTalkConfigurationError, match="loaded plugin"):
        declare(adapter, None)

    plugin = make_plugin()
    channel = declare(adapter, plugin)
    assert channel.self_id == "dingtalk:support:internal"
    assert channel.ready is False

    with pytest.raises(DingTalkConfigurationError, match="cleanup"):
        declare(adapter, plugin)


def test_event_mapping_namespaces_callers_and_sessions():
    payload = direct_payload()
    event = parse_callback(payload, connection_id="dingtalk:s:bot")
    assert event is not None
    assert event.get_plaintext() == "hello"
    assert json.loads(event.get_user_id()) == ["sender-1", "dingtalk:s:bot"]
    session = json.loads(event.get_session_id())
    assert session[:3] == ["dingtalk:s:bot", "private", "cid-1"]
    assert json.loads(session[3]) == ["sender-1", "dingtalk:s:bot"]
    assert event.is_tome() is True
    assert event.group_id is None
    assert event.user.name == "Tester"

    group = dict(payload)
    group.update(
        {
            "conversationType": "2",
            "isInAtList": False,
            "msgtype": "markdown",
            "markdown": {"title": "t", "text": "group text"},
        }
    )
    group_event = parse_callback(group, connection_id="dingtalk:s:bot")
    assert group_event is not None
    assert group_event.is_tome() is False
    assert group_event.get_plaintext() == "group text"
    assert group_event.conversation_type == "group"
    assert group_event.group_id == "cid-1"

    assert parse_callback({"msgtype": "image"}, connection_id="x") is None
    with pytest.raises(DingTalkProtocolError):
        parse_callback({"msgtype": "text", "text": {}}, connection_id="x")


def test_message_construction():
    message = Message("hello")
    assert message.extract_plain_text() == "hello"


@pytest.mark.anyio
async def test_missing_secret_fails_preparation():
    adapter = make_adapter()
    channel = declare(adapter, make_plugin(), secret_ref="DINGTALK_MISSING")
    with pytest.raises(DingTalkConfigurationError, match="DINGTALK_MISSING"):
        await channel.prepare()
    assert channel.ready is False


@pytest.mark.anyio
@pytest.mark.parametrize("conversation_type", ["1", "2"])
async def test_sdk_callback_dispatches_once(monkeypatch, conversation_type):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    payload = direct_payload()
    payload["conversationType"] = conversation_type
    dispatched = []
    received = anyio.Event()

    async def fake_dispatch(
        event,
        *,
        config,
        reply,
        invocation_context=None,
        matcher_filter=None,
        admitted_call=None,
        stack=None,
    ):
        assert config is adapter.driver.config
        assert reply.func == channel.send
        assert reply.args == (event,)
        assert invocation_context is event.user
        dispatched.append((get_event_channel(event), event, invocation_context))
        received.set()
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels.channel.dispatch_event",
        fake_dispatch,
    )

    async with running(adapter, channel):
        await open_streams(channel)
        messages = await callback(adapter, channel, payload)
        assert messages["code"] == 200
        with anyio.fail_after(2):
            await received.wait()

        assert len(dispatched) == 1
        _, event, caller = dispatched[0]
        assert event.get_plaintext() == "hello"
        assert json.loads(caller.id) == [
            "sender-1",
            "dingtalk:support:internal",
        ]
        assert caller.name == "Tester"
        assert caller.groups == frozenset()
        assert caller.roles == frozenset()
        if conversation_type == "2":
            assert event.group_id == "cid-1"

        duplicate = await callback(adapter, channel, payload)
        assert json.loads(duplicate["data"]) == {"response": "duplicate"}
        assert len(dispatched) == 1


@pytest.mark.anyio
async def test_sdk_callback_rejects_invalid_body_and_unready_owner(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    owner = make_plugin()
    channel = declare(adapter, owner)
    async with running(adapter, channel):
        await open_streams(channel)
        invalid = await callback(adapter, channel, {"msgtype": "text"})
        assert invalid["code"] == 400
        owner.status = PluginStatus.STOPPING
        refused = await callback(adapter, channel, direct_payload())
        assert refused["code"] == 500
        owner.status = PluginStatus.READY
        retried = await callback(adapter, channel, direct_payload())
        assert retried["code"] == 200


@pytest.mark.anyio
async def test_reply_uses_session_webhook(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    calls = []

    def respond(request):
        calls.append(
            {"url": str(request.url), "json": json.loads(request.content)}
        )
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        adapter = make_adapter()
        channel = declare(adapter, make_plugin())
        async with running(adapter, channel):
            await open_streams(channel)
            await use_transport(channel, http)
            payload = direct_payload()
            event = parse_callback(payload, connection_id=channel.self_id)
            assert event is not None
            await channel.send(event, "reply")
            assert calls[0]["url"] == payload["sessionWebhook"]
            assert calls[0]["json"] == {
                "msgtype": "text",
                "text": {"content": "reply"},
            }
            foreign = event.model_copy(update={"connection_id": "other-bot"})
            with pytest.raises(DingTalkSendError, match="another connection"):
                await channel.send(foreign, "reply")


@pytest.mark.anyio
async def test_channel_send_dispatches_attachment_to_media_sender(monkeypatch):
    from opsmesh.plugins.channels import Attachment, MessageSegment

    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        if request.url.path.endswith("/media/upload"):
            return httpx.Response(200, json={"media_id": "@media"})
        assert json.loads(request.content)["msgKey"] == "sampleFile"
        return httpx.Response(200, json={"processQueryKey": "sent-file"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        adapter = make_adapter()
        channel = declare(adapter, make_plugin())
        async with running(adapter, channel):
            await open_streams(channel)
            await use_transport(channel, http)
            payload = direct_payload()
            payload["senderStaffId"] = "staff"
            event = parse_callback(payload, connection_id=channel.self_id)
            attachment = event.attachments.add_bytes(
                Attachment.create("report.txt", "text/plain"), b"report"
            )
            result = await channel.send(
                event, MessageSegment.attachment(attachment)
            )
            assert result == {"processQueryKey": "sent-file"}
            assert len(calls) == 3


@pytest.mark.anyio
async def test_reply_failures_are_explicit(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)

    def respond(request):
        return httpx.Response(200, json={"errcode": 400, "errmsg": "bad"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        adapter = make_adapter()
        channel = declare(adapter, make_plugin())
        async with running(adapter, channel):
            await open_streams(channel)
            await use_transport(channel, http)
            payload = direct_payload()
            expired_payload = dict(payload)
            expired_payload["sessionWebhookExpiredTime"] = (
                int(time.time() * 1000) - 1000
            )
            expired = parse_callback(
                expired_payload, connection_id=channel.self_id
            )
            assert expired is not None
            with pytest.raises(DingTalkSendError, match="expired"):
                await channel.send(expired, "reply")

            missing_payload = dict(payload)
            missing_payload.pop("sessionWebhook")
            missing = parse_callback(
                missing_payload, connection_id=channel.self_id
            )
            assert missing is not None
            with pytest.raises(DingTalkSendError, match="session webhook"):
                await channel.send(missing, "reply")

            event = parse_callback(payload, connection_id=channel.self_id)
            assert event is not None
            with pytest.raises(ValueError, match="empty"):
                await channel.send(event, "  ")
            with pytest.raises(OperationError, match="400"):
                await channel.send(event, "reply")


@pytest.mark.anyio
async def test_shutdown_closes_streams_and_unregisters_channel(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    async with running(adapter, channel):
        await open_streams(channel)
        assert channel.ready
        assert channel._inbound_task is not None
    assert channel.state == "closed"
    assert channel._inbound_task is None
    result = await adapter.accept_callback(channel, direct_payload())
    assert result[0] == 500
    assert adapter.connections == ()


@pytest.mark.anyio
async def test_full_queue_releases_deduplication_for_retry(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    accepted = []
    outcomes = iter((False, True))

    def enqueue(item):
        accepted.append(item)
        return next(outcomes)

    async with running(adapter, channel):
        await open_streams(channel)
        monkeypatch.setattr(channel._queue, "put_nowait", enqueue)
        first = await callback(adapter, channel, direct_payload())
        retry = await callback(adapter, channel, direct_payload())
        duplicate = await callback(adapter, channel, direct_payload())
        assert first["code"] == 500
        assert retry["code"] == 200
        assert json.loads(duplicate["data"]) == {"response": "duplicate"}
        assert len(accepted) == 2


@pytest.mark.anyio
async def test_connection_only_dispatches_to_its_ready_owner(monkeypatch):
    from opsmesh.matchers import Matcher, MatcherSource

    adapter = make_adapter()
    owner = make_plugin()
    monkeypatch.setattr(
        registry,
        "get_plugin",
        lambda name: owner if name == "support" else None,
    )
    channel = declare(adapter, owner)
    event = parse_callback(direct_payload(), connection_id=channel.self_id)
    calls = []

    async def own():
        calls.append("support")

    async def other():
        calls.append("other")

    selected = Matcher.new(
        type_="message",
        handlers=[own],
        source=MatcherSource(plugin_id="support"),
    )
    unrelated = Matcher.new(
        type_="message",
        handlers=[other],
        source=MatcherSource(plugin_id="other"),
    )
    try:
        result = await channel.dispatch(event)
        result.raise_for_errors()
        assert calls == ["support"]
        owner.status = PluginStatus.STOPPING
        assert channel.owner_ready is False
    finally:
        selected.destroy()
        unrelated.destroy()


pytestmark = pytest.mark.usefixtures("dingtalk_sdk_transport")
