"""Verify QQ Bot API v2 Gateway and message behavior."""

import asyncio
import json
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

import anyio
import httpx
import pytest

from opsmesh.config import Config, Env
from opsmesh.dispatch.results import DispatchResult
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels import get_event_channel
from opsmesh.plugins.channels.qq import (
    GROUP_MESSAGE_CREATE,
    QQAdapter,
    QQConfigurationError,
    QQConnectionSettings,
    parse_callback,
)
from opsmesh.plugins.channels.qq.client import QQClient
from opsmesh.plugins.channels.qq.gateway import QQStream
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus

SECRET = "0123456789abcdef0123456789abcdef"


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


def make_adapter() -> QQAdapter:
    return QQAdapter(Driver(Env(), Config()))


def declare(adapter, plugin, name="primary", secret_ref="QQ_SECRET"):
    settings = QQConnectionSettings(app_id="12345", secret_ref=secret_ref)
    with owned_by(plugin):
        return adapter.declare(settings, name=name)


def attach_client(channel, http=None):
    channel._client = QQClient(
        channel.settings, SECRET, name=channel.self_id, http=http
    )
    return channel.client


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
    plugin = make_plugin()
    channel = declare(adapter, plugin)
    assert channel.self_id == "qq:support:primary"
    assert channel.ready is False
    with pytest.raises(QQConfigurationError, match="cleanup"):
        declare(adapter, plugin)


def test_event_mapping_supports_c2c_and_both_group_events():
    direct = parse_callback(message_payload(), connection_id="qq:support:bot")
    assert direct is not None
    assert json.loads(direct.get_user_id()) == ["user-1", "qq:support:bot"]
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
        client = QQClient(
            QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
            SECRET,
            name="one",
            http=http,
        )
        api = client.api
        assert await api.get_gateway_url() == "wss://api.bot.qq.com/ws/"
        assert await asyncio.to_thread(api.ensure_token_sync) == "token"
        assert calls == [(TOKEN_URL, {"appId": "123", "clientSecret": SECRET})]
        await client.close()
        assert api.access_token is None


@pytest.mark.anyio
async def test_sdk_gateway_preserves_intents_notices_and_sequences():
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
    stream = QQStream(channel)
    delivered = []
    connected = []
    original_connected = channel.inbound_task_connected

    def record_connected():
        connected.append(channel)
        original_connected()

    channel.inbound_task_connected = record_connected

    async def deliver(channel_, payload):
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
    with anyio.fail_after(2):
        while len(delivered) < len(frames):
            await anyio.sleep(0)
    assert delivered == frames
    assert connected == [channel]
    assert stream._session == ("session-1", 42)
    gateway._ws = None
    await gateway.stop()
    assert not gateway._pending
    await channel.client.close()


@pytest.mark.anyio
async def test_stream_stop_before_start_never_opens_network():
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
    stream = QQStream(channel)
    stream.stop()
    await stream.run()
    await stream.wait_closed()
    assert stream._scope is None
    await channel.client.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event_type", ["C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"]
)
async def test_adapter_admits_gateway_event_once(monkeypatch, event_type):
    monkeypatch.setenv("QQ_SECRET", SECRET)
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    dispatched = []

    async def fake_dispatch(event, **kwargs):
        dispatched.append(
            (get_event_channel(event), event, kwargs["invocation_context"])
        )
        return DispatchResult()

    monkeypatch.setattr(
        "opsmesh.plugins.channels.channel.dispatch_event",
        fake_dispatch,
    )
    await adapter.driver.startup()
    try:
        await channel.prepare()
        channel.mark_ready()
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
    finally:
        await channel.close()
        await adapter.driver.shutdown()


@pytest.mark.anyio
async def test_native_gateway_handshake_delivery_and_stop(monkeypatch):
    """Exercise native SDK listeners with an isolated socket transport."""
    import aiohttp
    from qqbot_agent_sdk import QQApiClient

    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
    monkeypatch.setattr(QQApiClient, "ensure_token_sync", lambda self: "token")
    stream = QQStream(channel)
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
        await channel.client.close()
    assert socket.closed
    assert stream.gateway._listen_task is None
    assert stream.gateway._heartbeat_task is None
    assert not stream.gateway._pending


@pytest.mark.anyio
async def test_sdk_helper_tasks_do_not_survive_shutdown():
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
    stream = QQStream(channel)
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
    await channel.client.close()


@pytest.mark.anyio
async def test_clean_gateway_close_enters_native_reconnect():
    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
    stream = QQStream(channel)
    stream.gateway._ws = SimpleNamespace(closed=True)
    stream.gateway._running = True
    with pytest.raises(ConnectionError, match="gateway closed"):
        await stream.gateway._read_events()
    await stream.gateway.stop()
    await channel.client.close()


@pytest.mark.anyio
async def test_stream_cancellation_closes_sdk_session(monkeypatch):
    from qqbot_agent_sdk import QQApiClient

    adapter = make_adapter()
    channel = declare(adapter, make_plugin())
    attach_client(channel)
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
        "opsmesh.plugins.channels.qq.gateway.aiohttp.ClientSession",
        lambda **kwargs: Session(),
    )
    stream = QQStream(channel)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(stream.run)
        with anyio.fail_after(2):
            await opened.wait()
            stream.stop()
            await stream.wait_closed()
    assert closed.is_set()
    assert socket.closed
    assert not stream.gateway._pending
    await channel.client.close()


@pytest.mark.anyio
async def test_native_api_preserves_safe_operation_errors(monkeypatch, caplog):
    from qqbot_agent_sdk import QQApiClient

    from opsmesh.plugins.channels.operations import OperationPermissionDenied

    monkeypatch.setattr(QQApiClient, "ensure_token_sync", lambda self: "token")

    def respond(request):
        return httpx.Response(
            403, json={"code": 123, "message": "private-response-secret"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        client = QQClient(
            QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
            SECRET,
            name="one",
            http=http,
        )
        with pytest.raises(OperationPermissionDenied) as caught:
            await client.api.request("GET", "/v2/groups/group/info")
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
