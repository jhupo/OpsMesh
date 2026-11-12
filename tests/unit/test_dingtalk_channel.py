"""Check DingTalk Stream admission, event mapping, and replies."""

import json
import time
from contextlib import contextmanager

import anyio
import pytest

from opsmesh.config import Config, Env
from opsmesh.dispatch.results import DispatchResult
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConfigurationError,
    DingTalkConnectionSettings,
    DingTalkMessage,
    DingTalkProtocolError,
    DingTalkSendError,
    DingTalkSettings,
    parse_callback,
)

SECRET = "dingtalk-test-secret"


class FakePlugin:
    """A minimal loaded plugin for declaration ownership."""

    def __init__(self, id_: str) -> None:
        self.ready = True
        self.id_ = id_
        self.cleanups = []


@contextmanager
def owned_by(plugin):
    token = registry._current_plugin.set(plugin)
    try:
        yield plugin
    finally:
        registry._current_plugin.reset(token)


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    async def post(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if not self.responses:
            raise AssertionError("unexpected vendor request")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def make_runtime() -> Driver:
    driver = Driver(Env(), Config())
    return driver


def make_adapter(client=None, **options) -> DingTalkAdapter:
    return DingTalkAdapter(
        make_runtime(),
        settings=DingTalkSettings(**options),
        http_client=client,
    )


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
        return adapter.declare(settings, name=name)


@pytest.fixture(autouse=True)
def connected_stream(monkeypatch):
    """Keep transport tests offline while exercising owned Stream tasks."""
    from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream

    async def receive(stream):
        stream.adapter.stream_connected(stream.channel)
        await anyio.sleep_forever()

    monkeypatch.setattr(DingTalkStream, "_receive", receive)


async def open_streams(adapter):
    await adapter.start_accepting()
    with anyio.fail_after(2):
        while any(c.state == "connecting" for c in adapter.declarations):
            await anyio.sleep(0)


async def callback(adapter, channel, payload):
    """Route an official SDK frame and capture its wire acknowledgement."""
    from dingtalk_stream import ChatbotMessage

    replies = []

    class Socket:
        async def send(self, value):
            replies.append(json.loads(value))

    stream = adapter._streams[channel]
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

    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    assert channel.self_id == "dingtalk:support:internal"
    assert channel.ready is False

    with pytest.raises(DingTalkConfigurationError, match="Duplicate"):
        declare(adapter, plugin)


def test_event_mapping_namespaces_callers_and_sessions():
    payload = direct_payload()
    event = parse_callback(payload, connection_id="dingtalk:s:bot")
    assert event is not None
    assert event.get_plaintext() == "hello"
    assert event.get_user_id() == "dingtalk:s:bot:sender-1"
    assert event.get_session_id() == "dingtalk:s:bot:conversation:cid-1"
    assert event.is_tome() is True
    assert event.group_ids == ()

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
    assert group_event.group_ids == ("dingtalk:s:bot:conversation:cid-1",)

    assert parse_callback({"msgtype": "image"}, connection_id="x") is None
    with pytest.raises(DingTalkProtocolError):
        parse_callback({"msgtype": "text", "text": {}}, connection_id="x")


def test_message_construction():
    message = DingTalkMessage("hello")
    assert message.extract_plain_text() == "hello"


@pytest.mark.anyio
async def test_startup_isolates_connections_without_secrets(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    good = declare(adapter, plugin)
    bad = declare(adapter, plugin, name="broken", secret_ref="DINGTALK_MISSING")
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        assert good.ready is True
        assert bad.ready is False
        assert "DINGTALK_MISSING" in (bad.error or "")
        assert good.self_id in driver.channels
        assert bad.self_id not in driver.channels
        assert adapter.accept_callback(bad, direct_payload())[0] == 500
        await adapter.shutdown()

    assert good.state == "closed"
    assert driver.channels == {}


@pytest.mark.anyio
async def test_sdk_callback_dispatches_once(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    dispatched = []
    received = anyio.Event()

    async def fake_dispatch(
        channel_, event, *, invocation_context=None, matcher_filter=None
    ):
        dispatched.append((channel_, event, invocation_context))
        received.set()
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels.dingtalk.adapter.dispatch_event",
        fake_dispatch,
    )
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        messages = await callback(adapter, channel, direct_payload())
        assert messages["code"] == 200
        with anyio.fail_after(2):
            await received.wait()

        assert len(dispatched) == 1
        _, event, caller = dispatched[0]
        assert event.get_plaintext() == "hello"
        assert caller.id == "dingtalk:support:internal:sender-1"
        assert caller.name == "Tester"
        assert caller.groups == frozenset()

        duplicate = await callback(adapter, channel, direct_payload())
        assert json.loads(duplicate["data"]) == {"response": "duplicate"}
        assert len(dispatched) == 1
        await adapter.shutdown()


@pytest.mark.anyio
async def test_sdk_callback_rejects_invalid_body_and_unready_owner(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    owner = FakePlugin("support")
    channel = declare(adapter, owner)
    async with adapter.driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        invalid = await callback(adapter, channel, {"msgtype": "text"})
        assert invalid["code"] == 400
        owner.ready = False
        refused = await callback(adapter, channel, direct_payload())
        assert refused["code"] == 500
        owner.ready = True
        retried = await callback(adapter, channel, direct_payload())
        assert retried["code"] == 200
        await adapter.shutdown()


@pytest.mark.anyio
async def test_reply_uses_session_webhook(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    client = FakeHTTP(FakeResponse({"errcode": 0, "errmsg": "ok"}))
    adapter = make_adapter(client)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        payload = direct_payload()
        event = parse_callback(payload, connection_id=channel.self_id)
        assert event is not None
        await channel.send(event, "reply")
        call_ = client.calls[0]
        assert call_["url"] == payload["sessionWebhook"]
        assert call_["json"] == {
            "msgtype": "text",
            "text": {"content": "reply"},
        }
        await adapter.shutdown()


@pytest.mark.anyio
async def test_reply_failures_are_explicit(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    client = FakeHTTP(FakeResponse({"errcode": 400, "errmsg": "bad"}))
    adapter = make_adapter(client)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        payload = direct_payload()
        expired_payload = dict(payload)
        expired_payload["sessionWebhookExpiredTime"] = (
            int(time.time() * 1000) - 1000
        )
        expired = parse_callback(expired_payload, connection_id=channel.self_id)
        assert expired is not None
        with pytest.raises(DingTalkSendError, match="expired"):
            await channel.send(expired, "reply")

        missing_payload = dict(payload)
        missing_payload.pop("sessionWebhook")
        missing = parse_callback(missing_payload, connection_id=channel.self_id)
        assert missing is not None
        with pytest.raises(DingTalkSendError, match="session webhook"):
            await channel.send(missing, "reply")

        event = parse_callback(payload, connection_id=channel.self_id)
        assert event is not None
        with pytest.raises(ValueError, match="empty"):
            await channel.send(event, "  ")
        with pytest.raises(DingTalkSendError, match="code 400"):
            await channel.send(event, "reply")
        await adapter.shutdown()


@pytest.mark.anyio
async def test_shutdown_closes_streams_and_unregisters_channel(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, FakePlugin("support"))
    async with adapter.driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        assert channel.ready
        await adapter.shutdown()
        assert adapter._streams == {}
        assert adapter.accept_callback(channel, direct_payload())[0] == 500
    assert channel.state == "closed"
    assert adapter.driver.channels == {}


@pytest.mark.anyio
async def test_full_queue_releases_deduplication_for_retry(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, FakePlugin("support"))
    accepted = []
    outcomes = iter((False, True))

    def enqueue(item):
        accepted.append(item)
        return next(outcomes)

    async with adapter.driver.lifespan():
        await adapter.startup()
        await open_streams(adapter)
        monkeypatch.setattr(adapter._queue, "put_nowait", enqueue)
        first = await callback(adapter, channel, direct_payload())
        retry = await callback(adapter, channel, direct_payload())
        duplicate = await callback(adapter, channel, direct_payload())
        assert first["code"] == 500
        assert retry["code"] == 200
        assert json.loads(duplicate["data"]) == {"response": "duplicate"}
        assert len(accepted) == 2
        await adapter.shutdown()


@pytest.mark.anyio
async def test_connection_only_dispatches_to_its_ready_owner(monkeypatch):
    from opsmesh.dispatch import dispatch_event
    from opsmesh.matchers import Matcher, MatcherSource

    adapter = make_adapter()
    owner = FakePlugin("support")
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
        result = await dispatch_event(
            channel, event, matcher_filter=channel.accepts_matcher
        )
        result.raise_for_errors()
        owner.ready = False
        result = await dispatch_event(
            channel, event, matcher_filter=channel.accepts_matcher
        )
        assert result.handled == 0
        assert calls == ["support"]
    finally:
        selected.destroy()
        unrelated.destroy()
