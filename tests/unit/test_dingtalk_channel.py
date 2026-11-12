"""Check DingTalk callback verification, event mapping, and replies."""

import base64
import hashlib
import hmac
import json
import time
from contextlib import contextmanager

import anyio
import pytest

from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework.config import Config, Env
from opsmesh.framework.dispatch.results import DispatchResult
from opsmesh.framework.plugins import registry
from opsmesh_channels.dingtalk import (
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


def make_driver() -> HTTPDriver:
    return HTTPDriver(
        Env(), Config(driver_options={"http": {"enabled": False}})
    )


def make_adapter(client=None, **options) -> DingTalkAdapter:
    return DingTalkAdapter(
        make_driver(),
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


def scope(path: str, body: bytes, headers=()):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": list(headers),
        "scheme": "http",
        "server": ("testserver", 80),
    }, [{"type": "http.request", "body": body, "more_body": False}]


async def call(driver, request):
    request_scope, queue = request
    sent = []

    async def receive():
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await driver(request_scope, receive, send)
    return sent


def response_payload(messages):
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return json.loads(body)


def signature(secret: str, timestamp: str) -> str:
    message = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(
        hmac.new(secret.encode(), message, hashlib.sha256).digest()
    ).decode()


def signed(path: str, payload: dict, secret: str = SECRET, timestamp=None):
    body = json.dumps(payload).encode()
    stamp = str(int(time.time() * 1000) if timestamp is None else timestamp)
    return scope(
        path,
        body,
        [
            (b"timestamp", stamp.encode()),
            (b"sign", signature(secret, stamp).encode()),
        ],
    )


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
    assert channel.path == "/channels/dingtalk/support/internal"
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
        assert good.ready is True
        assert bad.ready is False
        assert "DINGTALK_MISSING" in (bad.error or "")
        assert good.self_id in driver.channels
        assert bad.self_id not in driver.channels
        messages = await call(driver, signed(bad.path, direct_payload()))
        assert messages[0]["status"] == 503
        await adapter.shutdown()

    assert good.state == "closed"
    assert driver.channels == {}


@pytest.mark.anyio
async def test_signed_callback_dispatches_once(monkeypatch):
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
        "opsmesh_channels.dingtalk.adapter.dispatch_event",
        fake_dispatch,
    )
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        messages = await call(driver, signed(channel.path, direct_payload()))
        assert response_payload(messages) == {"status": "ok"}
        with anyio.fail_after(2):
            await received.wait()

        assert len(dispatched) == 1
        _, event, caller = dispatched[0]
        assert event.get_plaintext() == "hello"
        assert caller.id == "dingtalk:support:internal:sender-1"
        assert caller.name == "Tester"
        assert caller.groups == frozenset()

        duplicate = await call(driver, signed(channel.path, direct_payload()))
        assert response_payload(duplicate) == {"status": "duplicate"}
        assert len(dispatched) == 1
        await adapter.shutdown()


@pytest.mark.anyio
async def test_callback_rejects_bad_signatures(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        assert (
            await call(
                driver,
                signed(
                    channel.path,
                    direct_payload(),
                    timestamp=int(time.time() * 1000) - 3600000,
                ),
            )
        )[0]["status"] == 401

        body = json.dumps(direct_payload()).encode()
        unsigned = scope(channel.path, body)
        assert (await call(driver, unsigned))[0]["status"] == 401

        stamp = str(int(time.time() * 1000))
        wrong = scope(
            channel.path,
            body,
            [
                (b"timestamp", stamp.encode()),
                (b"sign", b"invalid"),
            ],
        )
        assert (await call(driver, wrong))[0]["status"] == 401

        invalid_json = scope(
            channel.path,
            b"not json",
            [
                (b"timestamp", stamp.encode()),
                (b"sign", signature(SECRET, stamp).encode()),
            ],
        )
        assert (await call(driver, invalid_json))[0]["status"] == 400
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
async def test_shutdown_removes_callback_routes(monkeypatch):
    monkeypatch.setenv("DINGTALK_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        assert (await call(driver, signed(channel.path, direct_payload())))[0][
            "status"
        ] == 200
        await adapter.shutdown()
        missing = await call(driver, signed(channel.path, direct_payload()))
        assert missing[0]["status"] == 404

    assert channel.state == "closed"
    assert channel.ready is False


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
        monkeypatch.setattr(adapter._queue, "put_nowait", enqueue)
        first = await call(
            adapter.driver, signed(channel.path, direct_payload())
        )
        retry = await call(
            adapter.driver, signed(channel.path, direct_payload())
        )
        duplicate = await call(
            adapter.driver, signed(channel.path, direct_payload())
        )
        assert first[0]["status"] == 503
        assert response_payload(retry) == {"status": "ok"}
        assert response_payload(duplicate) == {"status": "duplicate"}
        assert len(accepted) == 2
        await adapter.shutdown()


@pytest.mark.anyio
async def test_connection_only_dispatches_to_its_ready_owner(monkeypatch):
    from opsmesh.framework.dispatch import dispatch_event
    from opsmesh.framework.matchers import Matcher, MatcherSource

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
