"""Check QQ callback verification, event mapping, and replies."""

import json
import time
from contextlib import contextmanager

import anyio
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from opsmesh.config import Config, Env
from opsmesh.dispatch.results import DispatchResult
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels.messages import Message, MessageSegment
from opsmesh.plugins.channels.qq import (
    READY,
    QQAdapter,
    QQConfigurationError,
    QQConnectionSettings,
    QQProtocolError,
    QQSendError,
    QQSettings,
    parse_callback,
)
from opsmesh.plugins.model import PluginStatus

SECRET = "0123456789abcdef0123456789abcdef"


class FakePlugin:
    """A minimal loaded plugin for declaration ownership."""

    def __init__(self, id_: str) -> None:
        self.ready = True
        self.id_ = id_
        self.cleanups = []
        self.status = PluginStatus.LOADED


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

    async def post(
        self,
        url,
        *,
        json=None,
        headers=None,
        timeout=None,
        follow_redirects=False,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if not self.responses:
            raise AssertionError("unexpected vendor request")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_parallel_sends_share_token_refresh():
    from opsmesh.plugins.channels.qq.client import QQClient

    class YieldingHTTP(FakeHTTP):
        async def post(self, *args, **kwargs):
            await anyio.sleep(0)
            return await super().post(*args, **kwargs)

    http = YieldingHTTP(
        FakeResponse({"access_token": "shared-token", "expires_in": 7200})
    )
    client = QQClient(QQSettings(), http)
    tokens = []

    async def request_token():
        tokens.append(await client.access_token("connection", "123", SECRET))

    async with anyio.create_task_group() as tasks:
        for _ in range(8):
            tasks.start_soon(request_token)

    assert tokens == ["shared-token"] * 8
    assert len(http.calls) == 1


def make_runtime() -> Driver:
    driver = Driver(Env(), Config())
    return driver


def make_adapter(client=None, **options) -> QQAdapter:
    return QQAdapter(
        make_runtime(),
        settings=QQSettings(**options),
        http_client=client,
    )


def declare(adapter, plugin, name="primary", secret_ref="QQ_SECRET"):
    settings = QQConnectionSettings(app_id="12345", secret_ref=secret_ref)
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


async def call(server, request):
    request_scope, queue = request
    sent = []

    async def receive():
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await server.app(request_scope, receive, send)
    return sent


def response_payload(messages):
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return json.loads(body)


def signed(path: str, payload: dict, secret: str = SECRET, timestamp=None):
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    key = Ed25519PrivateKey.from_private_bytes(secret.encode())
    signature = key.sign(stamp.encode() + body).hex()
    return scope(
        path,
        body,
        [
            (b"x-signature-ed25519", signature.encode()),
            (b"x-signature-timestamp", stamp.encode()),
        ],
    )


def c2c_payload(event_id="event-1", message_id="msg-1") -> dict:
    return {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "id": event_id,
        "d": {
            "id": message_id,
            "content": "hello",
            "timestamp": 1700000000,
            "author": {"user_openid": "user-1"},
        },
    }


def group_payload() -> dict:
    return {
        "op": 0,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "id": "event-group",
        "d": {
            "id": "msg-group",
            "content": "/help",
            "timestamp": 1700000000,
            "group_openid": "group-1",
            "author": {"member_openid": "member-1"},
        },
    }


def test_declaration_requires_an_owning_plugin():
    adapter = make_adapter()

    with pytest.raises(QQConfigurationError, match="loaded plugin"):
        declare(adapter, None)

    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    assert channel.self_id == "qq:support:primary"
    assert channel.path == "/channels/qq/support/primary"
    assert channel.owner_id == "support"
    assert channel.ready is False

    with pytest.raises(QQConfigurationError, match="Duplicate"):
        declare(adapter, plugin)


def test_event_mapping_namespaces_callers_and_sessions():
    direct = parse_callback(c2c_payload(), connection_id="qq:support:bot")
    assert direct is not None
    assert direct.get_type() == "message"
    assert direct.get_event_name() == "qq.message"
    assert direct.get_plaintext() == "hello"
    assert direct.get_user_id() == "qq:support:bot:user-1"
    assert json.loads(direct.get_session_id()) == [
        "qq",
        "qq:support:bot",
        "private",
        None,
        "user-1",
    ]
    assert direct.is_tome() is True
    assert direct.group_ids == ()

    group = parse_callback(group_payload(), connection_id="qq:support:bot")
    assert group is not None
    assert json.loads(group.get_session_id()) == [
        "qq",
        "qq:support:bot",
        "group",
        "group-1",
        "member-1",
    ]
    assert group.group_ids == ("qq:support:bot:group:group-1",)

    assert parse_callback({"op": 0, "t": "READY"}, connection_id="x") is None
    with pytest.raises(QQProtocolError):
        parse_callback(
            {"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}},
            connection_id="x",
        )


def test_message_construction_and_outgoing_rules():
    message = Message("hello")
    assert message.extract_plain_text() == "hello"
    assert MessageSegment("text", {"text": "world"}).is_text() is True


@pytest.mark.anyio
async def test_startup_isolates_connections_without_secrets(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    good = declare(adapter, plugin)
    bad = declare(adapter, plugin, name="broken", secret_ref="QQ_MISSING")

    driver = adapter.driver
    async with driver.lifespan():
        await adapter.startup()
        assert good.ready is True
        assert bad.ready is False
        assert bad.state == "failed"
        assert "QQ_MISSING" in (bad.error or "")
        assert good.self_id in driver.channels
        assert bad.self_id not in driver.channels

        messages = await call(adapter.server, signed(bad.path, c2c_payload()))
        assert messages[0]["status"] == 503

        await adapter.shutdown()

    assert good.state == "closed"
    assert driver.channels == {}


@pytest.mark.anyio
async def test_signed_callback_dispatches_once(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    dispatched = []
    received = anyio.Event()

    async def fake_dispatch(
        channel_,
        event,
        *,
        invocation_context=None,
        matcher_filter=None,
        admitted_call=None,
        stack=None,
    ):
        dispatched.append((channel_, event, invocation_context))
        received.set()
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels.qq.adapter.dispatch_event", fake_dispatch
    )

    driver = adapter.driver
    async with driver.lifespan():
        await adapter.startup()
        messages = await call(
            adapter.server, signed(channel.path, c2c_payload())
        )
        assert response_payload(messages) == {"status": "ok"}
        with anyio.fail_after(2):
            await received.wait()

        assert len(dispatched) == 1
        _, event, caller = dispatched[0]
        assert event.get_plaintext() == "hello"
        assert caller.id == "qq:support:primary:user-1"
        assert caller.groups == frozenset()
        assert caller.roles == frozenset()

        duplicate = await call(
            adapter.server, signed(channel.path, c2c_payload())
        )
        assert response_payload(duplicate) == {"status": "duplicate"}
        assert len(dispatched) == 1
        await adapter.shutdown()


@pytest.mark.anyio
async def test_group_callback_passes_group_context(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    dispatched = []
    received = anyio.Event()

    async def fake_dispatch(
        channel_,
        event,
        *,
        invocation_context=None,
        matcher_filter=None,
        admitted_call=None,
        stack=None,
    ):
        dispatched.append(invocation_context)
        received.set()
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels.qq.adapter.dispatch_event", fake_dispatch
    )

    driver = adapter.driver
    async with driver.lifespan():
        await adapter.startup()
        await call(adapter.server, signed(channel.path, group_payload()))
        with anyio.fail_after(2):
            await received.wait()
        assert dispatched[0].id == "qq:support:primary:member-1"
        assert dispatched[0].groups == frozenset(
            {"qq:support:primary:group:group-1"}
        )
        await adapter.shutdown()


@pytest.mark.anyio
async def test_callback_rejects_bad_signatures_and_payloads(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        stale = await call(
            adapter.server,
            signed(
                channel.path,
                c2c_payload(),
                timestamp=int(time.time()) - 1000,
            ),
        )
        assert stale[0]["status"] == 401

        body = json.dumps(c2c_payload()).encode()
        unsigned = scope(channel.path, body)
        assert (await call(adapter.server, unsigned))[0]["status"] == 401

        stamp = str(int(time.time()))
        bad_header = scope(
            channel.path,
            body,
            [
                (b"x-signature-ed25519", b"00" * 64),
                (b"x-signature-timestamp", stamp.encode()),
            ],
        )
        assert (await call(adapter.server, bad_header))[0]["status"] == 401

        invalid_json = signed(channel.path, {})
        invalid_json[1][0]["body"] = b"not json"
        # Re-sign the replaced body so only JSON parsing fails.
        key = Ed25519PrivateKey.from_private_bytes(SECRET.encode())
        signature = key.sign(stamp.encode() + b"not json").hex()
        invalid_json[0]["headers"] = [
            (b"x-signature-ed25519", signature.encode()),
            (b"x-signature-timestamp", stamp.encode()),
        ]
        assert (await call(adapter.server, invalid_json))[0]["status"] == 400
        await adapter.shutdown()


@pytest.mark.anyio
async def test_handshake_signs_the_challenge(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        stamp = str(int(time.time()))
        payload = {
            "op": 13,
            "d": {"plain_token": "token-1", "event_ts": stamp},
        }
        request = scope(channel.path, json.dumps(payload).encode())
        messages = await call(adapter.server, request)
        result = response_payload(messages)
        assert result["plain_token"] == "token-1"
        public = Ed25519PrivateKey.from_private_bytes(
            SECRET.encode()
        ).public_key()
        public.verify(
            bytes.fromhex(result["signature"]),
            f"{stamp}token-1".encode(),
        )

        stale = {
            "op": 13,
            "d": {
                "plain_token": "token-1",
                "event_ts": str(int(time.time()) - 1000),
            },
        }
        rejected = await call(
            adapter.server, scope(channel.path, json.dumps(stale).encode())
        )
        assert rejected[0]["status"] == 400
        await adapter.shutdown()


@pytest.mark.anyio
async def test_replies_use_cached_tokens_and_sequence(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    client = FakeHTTP(
        FakeResponse({"access_token": "token-1", "expires_in": 7200}),
        FakeResponse({"id": "sent-1"}),
        FakeResponse({"id": "sent-2"}),
    )
    adapter = make_adapter(client)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        event = parse_callback(c2c_payload(), connection_id=channel.self_id)
        assert event is not None
        await channel.send(event, "reply")
        await channel.send(event, "again")

        token_call, first, second = client.calls
        assert token_call["url"].endswith("/getAppAccessToken")
        assert token_call["json"] == {
            "appId": "12345",
            "clientSecret": SECRET,
        }
        assert first["url"].endswith("/v2/users/user-1/messages")
        assert first["headers"]["Authorization"] == "QQBot token-1"
        assert first["json"]["content"] == "reply"
        assert first["json"]["msg_seq"] == 1
        assert second["json"]["msg_seq"] == 2
        foreign = event.model_copy(update={"connection_id": "other-bot"})
        with pytest.raises(QQSendError, match="another connection"):
            await channel.send(foreign, "reply")
        await adapter.shutdown()


@pytest.mark.anyio
async def test_channel_send_dispatches_attachment_to_media_sender(monkeypatch):
    from opsmesh.plugins.channels import Attachment

    monkeypatch.setenv("QQ_SECRET", SECRET)
    client = FakeHTTP(
        FakeResponse({"access_token": "token"}),
        FakeResponse({"file_info": "info"}),
        FakeResponse({"id": "sent-file"}),
    )
    adapter = make_adapter(client)
    channel = declare(adapter, FakePlugin("support"))
    async with adapter.driver.lifespan():
        await adapter.startup()
        event = parse_callback(c2c_payload(), connection_id=channel.self_id)
        attachment = event.attachments.add_bytes(
            Attachment.create("report.txt", "text/plain"), b"report"
        )
        result = await channel.send(
            event, MessageSegment.attachment(attachment)
        )
        assert result == {"id": "sent-file"}
        assert client.calls[1]["json"]["file_type"] == 4
        assert client.calls[2]["json"]["media"] == {"file_info": "info"}
        await adapter.shutdown()


@pytest.mark.anyio
async def test_group_reply_uses_group_endpoint(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    client = FakeHTTP(
        FakeResponse({"access_token": "token-1", "expires_in": 7200}),
        FakeResponse({"id": "sent-1"}),
    )
    adapter = make_adapter(client)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        event = parse_callback(group_payload(), connection_id=channel.self_id)
        assert event is not None
        await channel.send(event, "reply")
        assert client.calls[1]["url"].endswith("/v2/groups/group-1/messages")
        await adapter.shutdown()


@pytest.mark.anyio
async def test_send_rejects_invalid_messages_and_vendor_errors(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    client = FakeHTTP(
        FakeResponse({"access_token": "token-1", "expires_in": 7200}),
        FakeResponse({}, status_code=500),
    )
    adapter = make_adapter(client)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        event = parse_callback(c2c_payload(), connection_id=channel.self_id)
        assert event is not None
        with pytest.raises(ValueError, match="empty"):
            await channel.send(event, "   ")
        with pytest.raises(ValueError, match="limited"):
            await channel.send(event, "x" * 2001)
        with pytest.raises(TypeError):
            await channel.send(event, object())
        with pytest.raises(QQSendError, match="status 500"):
            await channel.send(event, "reply")
        await adapter.shutdown()


@pytest.mark.anyio
async def test_shutdown_removes_callback_routes(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    driver = adapter.driver

    async with driver.lifespan():
        await adapter.startup()
        assert (
            await call(adapter.server, signed(channel.path, c2c_payload()))
        )[0]["status"] == 200
        await adapter.shutdown()
        missing = await call(
            adapter.server, signed(channel.path, c2c_payload())
        )
        assert missing[0]["status"] == 404

    assert channel.state == "closed"
    assert channel.ready is False
    assert channel.state != READY


@pytest.mark.anyio
async def test_full_queue_releases_deduplication_for_retry(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", SECRET)
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
        first = await call(adapter.server, signed(channel.path, c2c_payload()))
        retry = await call(adapter.server, signed(channel.path, c2c_payload()))
        duplicate = await call(
            adapter.server, signed(channel.path, c2c_payload())
        )
        assert first[0]["status"] == 503
        assert response_payload(retry) == {"status": "ok"}
        assert response_payload(duplicate) == {"status": "duplicate"}
        assert len(accepted) == 2
        await adapter.shutdown()


@pytest.mark.anyio
async def test_connection_only_dispatches_to_its_ready_owner(monkeypatch):
    from types import ModuleType

    from opsmesh.dispatch import dispatch_event
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.plugins.model import Plugin

    adapter = make_adapter()
    owner = Plugin(
        name="support",
        module=ModuleType("support"),
        module_name="support",
        manager=None,
        status=PluginStatus.READY,
    )
    monkeypatch.setattr(
        registry,
        "get_plugin",
        lambda name: owner if name == "support" else None,
    )
    channel = declare(adapter, owner)
    event = parse_callback(c2c_payload(), connection_id=channel.self_id)
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
        owner.status = PluginStatus.STOPPING
        result = await dispatch_event(
            channel, event, matcher_filter=channel.accepts_matcher
        )
        assert result.handled == 0
        assert calls == ["support"]
    finally:
        selected.destroy()
        unrelated.destroy()
