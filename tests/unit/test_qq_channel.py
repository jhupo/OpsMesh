"""Verify QQ Bot API v2 Gateway and message behavior."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import anyio
import httpx
import pytest

from opsmesh.config import Config, Env
from opsmesh.dispatch.results import DispatchResult
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels.qq import (
    GROUP_MESSAGE_CREATE,
    QQAdapter,
    QQConfigurationError,
    QQConnectionSettings,
    QQSettings,
    parse_callback,
)
from opsmesh.plugins.channels.qq.stream import QQStream
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


def make_runtime() -> Driver:
    return Driver(Env(), Config())


def make_adapter(client=None, **options) -> QQAdapter:
    return QQAdapter(
        make_runtime(), settings=QQSettings(**options), http_client=client
    )


def declare(adapter, plugin, name="primary", secret_ref="QQ_SECRET"):
    settings = QQConnectionSettings(app_id="12345", secret_ref=secret_ref)
    with owned_by(plugin):
        return adapter.declare(settings, name=name)


def message_payload(event_type="C2C_MESSAGE_CREATE", *, sequence=None):
    data = {
        "id": "msg-1",
        "content": "hello",
        "timestamp": "2026-07-21T10:00:00+08:00",
        "author": {"user_openid": "user-1", "username": "Alice"},
    }
    if event_type in {"GROUP_AT_MESSAGE_CREATE", GROUP_MESSAGE_CREATE}:
        data["group_openid"] = "group-1"
        data["author"] = {"member_openid": "member-1", "username": "Bob"}
    if sequence is not None:
        data["msg_seq"] = sequence
    return {"op": 0, "t": event_type, "s": 10, "d": data}


def test_declaration_requires_an_owning_plugin():
    adapter = make_adapter()
    with pytest.raises(QQConfigurationError, match="loaded plugin"):
        declare(adapter, None)
    channel = declare(adapter, FakePlugin("support"))
    assert channel.self_id == "qq:support:primary"
    assert channel.ready is False


def test_event_mapping_supports_c2c_and_both_group_events():
    direct = parse_callback(message_payload(), connection_id="qq:support:bot")
    assert direct is not None
    assert direct.get_user_id() == "qq:support:bot:user-1"
    assert direct.user_name == "Alice"
    assert direct.timestamp == "2026-07-21T10:00:00+08:00"

    for event_type in ("GROUP_AT_MESSAGE_CREATE", GROUP_MESSAGE_CREATE):
        group = parse_callback(
            message_payload(event_type, sequence=2),
            connection_id="qq:support:bot",
        )
        assert group is not None
        assert group.group_id == "group-1"
        assert group.event_id == "msg-1:2"


def test_gateway_notice_uses_sequence_when_outer_id_is_absent():
    event = parse_callback(
        {
            "op": 0,
            "s": 42,
            "t": "GROUP_ADD_ROBOT",
            "d": {"group_openid": "group-1", "op_member_openid": "user-1"},
        },
        connection_id="qq:support:bot",
    )
    assert event is not None
    assert event.event_id == "GROUP_ADD_ROBOT:42"


@pytest.mark.anyio
async def test_native_token_cache_is_shared_with_gateway(monkeypatch):
    from qqbot_agent_sdk.api_client import API_BASE, TOKEN_URL

    from opsmesh.plugins.channels.qq.client import QQClient

    calls = []

    def token(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return httpx.Response(
            200,
            json={"access_token": "token", "expires_in": 7200},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", token)

    def gateway(request):
        assert str(request.url) == API_BASE + "/gateway"
        assert request.headers["Authorization"] == "QQBot token"
        return httpx.Response(200, json={"url": "wss://api.bot.qq.com/ws/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(gateway)
    ) as http:
        client = QQClient(QQSettings(), http)
        channel = SimpleNamespace(
            self_id="one",
            settings=QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
        )
        api = client.account(channel, SECRET)
        assert await api.get_gateway_url() == "wss://api.bot.qq.com/ws/"
        assert await asyncio.to_thread(api.ensure_token_sync) == "token"
        assert calls == [(TOKEN_URL, {"appId": "123", "clientSecret": SECRET})]
        assert client.account(channel, SECRET) is api
        other = SimpleNamespace(self_id="two", settings=channel.settings)
        assert client.account(other, SECRET) is not api
        await client.close()
        assert api.access_token is None


@pytest.mark.anyio
async def test_sdk_gateway_preserves_intents_notices_and_sequences():
    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    stream = QQStream(channel, SECRET)
    delivered = []
    connected = []
    adapter.stream_connected = lambda value: connected.append(value)

    async def deliver(value, payload):
        delivered.append(payload)

    adapter.accept_event = deliver
    gateway = stream.gateway
    sent = []

    class Socket:
        closed = False

        async def send_json(self, payload):
            sent.append(payload)

    gateway._ws = Socket()
    await gateway._send_ws_json({"op": 2, "d": {"intents": 0}}, "Identify")
    assert sent[0]["d"]["intents"] == channel.settings.intents
    gateway._dispatch_payload(
        {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "session-1"}}
    )
    frames = [
        message_payload("GROUP_MESSAGE_CREATE", sequence=1),
        message_payload("GROUP_MESSAGE_CREATE", sequence=2),
        {
            "op": 0,
            "s": 42,
            "t": "GROUP_JOIN_REQUEST",
            "d": {"group_openid": "group-1", "member_openid": "member-1"},
        },
    ]
    for frame in frames:
        gateway._dispatch_payload(frame)
    await asyncio.sleep(0)
    assert delivered == frames
    assert connected == [channel]
    assert stream._session == ("session-1", 42)
    gateway._ws = None
    await gateway.stop()
    assert not gateway._pending
    await adapter.client.close()


@pytest.mark.anyio
async def test_stream_stop_before_start_never_opens_network():
    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    stream = QQStream(channel, SECRET)
    stream.stop()
    await stream.run()
    await stream.wait_closed()
    assert stream._scope is None
    await adapter.client.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event_type", ["C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"]
)
async def test_adapter_admits_gateway_event_once(monkeypatch, event_type):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter(enabled=False, concurrency=1)
    channel = declare(adapter, FakePlugin("support"))
    channel.mark_ready()
    adapter._accepting = True
    dispatched = []

    async def fake_dispatch(channel_, event, **kwargs):
        dispatched.append((channel_, event, kwargs["invocation_context"]))
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels._work.dispatch_event", fake_dispatch
    )
    async with adapter.driver.lifespan():
        await adapter.startup()
        await adapter.accept_event(channel, message_payload(event_type))
        with anyio.fail_after(2):
            while not dispatched:
                await anyio.sleep(0)
        _, event, caller = dispatched[0]
        assert event.get_plaintext() == "hello"
        assert caller.groups == frozenset()
        assert caller.roles == frozenset()
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            assert event.group_id == "group-1"
        await adapter.shutdown()


@pytest.mark.anyio
async def test_native_gateway_handshake_delivery_and_stop(monkeypatch):
    """Exercise native SDK listeners with an isolated socket transport."""
    import aiohttp
    from qqbot_agent_sdk import QQApiClient

    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    monkeypatch.setattr(QQApiClient, "ensure_token_sync", lambda self: "token")
    stream = QQStream(channel, SECRET)
    received = []
    frames = asyncio.Queue()
    sent = []

    class Socket:
        closed = False

        async def send_json(self, payload):
            sent.append(payload)

        async def receive(self):
            return SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps(await frames.get()),
            )

        async def close(self):
            self.closed = True

    socket = Socket()

    class Session:
        async def ws_connect(self, *args, **kwargs):
            return socket

    async def deliver(_channel, payload):
        received.append(payload)

    monkeypatch.setattr(adapter, "accept_event", deliver)
    await stream.gateway.open("wss://example.invalid/gateway", Session())
    stream.gateway.start_listeners()
    try:
        await frames.put({"op": 10, "d": {"heartbeat_interval": 60000}})
        with anyio.fail_after(2):
            while not sent:
                await anyio.sleep(0)
        assert sent[0]["op"] == 2
        assert sent[0]["d"]["token"] == "QQBot token"
        await frames.put(
            {
                "op": 0,
                "s": 1,
                "t": "READY",
                "d": {"session_id": "native-session"},
            }
        )
        await frames.put(message_payload())
        with anyio.fail_after(2):
            while not received:
                await anyio.sleep(0)
        assert channel.ready
        assert received == [message_payload()]
        assert stream._session == ("native-session", 10)
    finally:
        await stream.gateway.stop()
        await adapter.client.close()
    assert socket.closed
    assert stream.gateway._listen_task is None
    assert stream.gateway._heartbeat_task is None
    assert not stream.gateway._pending


@pytest.mark.anyio
async def test_sdk_helper_tasks_do_not_survive_shutdown():
    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    stream = QQStream(channel, SECRET)
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def pending():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            exited.set()

    stream.gateway._create_task(pending())
    await entered.wait()
    await stream.gateway.stop()
    assert exited.is_set()
    assert not stream.gateway._pending
    await adapter.client.close()


@pytest.mark.anyio
async def test_clean_gateway_close_enters_native_reconnect():
    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    stream = QQStream(channel, SECRET)
    stream.gateway._ws = SimpleNamespace(closed=True)
    stream.gateway._running = True
    with pytest.raises(ConnectionError, match="gateway closed"):
        await stream.gateway._read_events()
    await stream.gateway.stop()
    await adapter.client.close()


@pytest.mark.anyio
async def test_stream_cancellation_closes_sdk_session(monkeypatch):
    from qqbot_agent_sdk import QQApiClient

    adapter = make_adapter(enabled=False)
    adapter.client.open()
    channel = declare(adapter, FakePlugin("support"))
    opened = asyncio.Event()
    closed = asyncio.Event()
    frames = asyncio.Queue()

    class Socket:
        closed = False

        async def receive(self):
            return await frames.get()

        async def close(self):
            self.closed = True

    socket = Socket()

    class Session:
        async def ws_connect(self, *args, **kwargs):
            opened.set()
            return socket

        async def close(self):
            await anyio.sleep(0)
            closed.set()

    async def gateway_url(self):
        return "wss://example.invalid/gateway"

    monkeypatch.setattr(QQApiClient, "get_gateway_url", gateway_url)
    monkeypatch.setattr(
        "opsmesh.plugins.channels.qq.stream.aiohttp.ClientSession",
        lambda **kwargs: Session(),
    )
    stream = QQStream(channel, SECRET)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(stream.run)
        with anyio.fail_after(2):
            await opened.wait()
            stream.stop()
            await stream.wait_closed()
    assert closed.is_set()
    assert socket.closed
    assert not stream.gateway._pending
    await adapter.client.close()


@pytest.mark.anyio
async def test_native_api_preserves_safe_operation_errors(monkeypatch, caplog):
    from qqbot_agent_sdk import QQApiClient

    from opsmesh.plugins.channels.operations import OperationPermissionDenied
    from opsmesh.plugins.channels.qq.client import QQClient

    monkeypatch.setattr(QQApiClient, "ensure_token_sync", lambda self: "token")

    def respond(request):
        return httpx.Response(
            403, json={"code": 123, "message": "private-response-secret"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        client = QQClient(QQSettings(), http)
        channel = SimpleNamespace(
            self_id="one",
            settings=QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
        )
        api = client.account(channel, SECRET)
        with pytest.raises(OperationPermissionDenied) as caught:
            await api.request("GET", "/v2/groups/group/info")
        assert caught.value.status == 403
        assert caught.value.code == "123"
        assert "private-response-secret" not in caplog.text


@pytest.mark.parametrize(
    "event_type, addressed",
    [
        ("C2C_MESSAGE_CREATE", True),
        ("GROUP_AT_MESSAGE_CREATE", True),
        ("GROUP_MESSAGE_CREATE", False),
    ],
)
def test_native_message_target_controls_to_me(event_type, addressed):
    event = parse_callback(
        message_payload(event_type), connection_id="qq:owner:bot"
    )
    assert event.is_tome() is addressed
