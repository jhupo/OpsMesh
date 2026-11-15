"""Verify QQ Bot API v2 Gateway and message behavior."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import anyio
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

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    async def aclose(self):
        return None


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
async def test_gateway_url_uses_official_token_and_gateway_endpoints():
    http = FakeHTTP(
        FakeResponse({"access_token": "token", "expires_in": 7200}),
        FakeResponse({"url": "wss://api.bot.qq.com/websocket/"}),
    )
    from opsmesh.plugins.channels.qq.client import QQClient

    client = QQClient(QQSettings(), http)
    client.open()
    assert (
        await client.gateway_url("qq:support:bot", "123", SECRET)
        == "wss://api.bot.qq.com/websocket/"
    )
    assert http.calls[0][1] == "https://api.bot.qq.com/app/getAppAccessToken"
    assert http.calls[1][1] == "https://api.bot.qq.com/gateway"
    assert http.calls[1][2]["headers"]["Authorization"] == "QQBot token"
    await client.close()


class FakeSocket:
    """Capture Gateway frames sent by authentication and heartbeat code."""

    def __init__(self) -> None:
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


@pytest.mark.anyio
async def test_gateway_authentication_identifies_with_intents():
    stream = object.__new__(QQStream)
    stream.channel = SimpleNamespace(
        self_id="qq:support:bot",
        settings=QQConnectionSettings(
            app_id="123", secret_ref="QQ_SECRET", intents=1 << 25
        ),
    )
    stream.secret = SECRET
    stream.adapter = SimpleNamespace(
        client=SimpleNamespace(
            access_token=lambda *args, **kwargs: _token("access-token")
        )
    )
    stream._session_id = None
    stream._sequence = None
    socket = FakeSocket()
    await stream._authenticate(socket)
    assert socket.sent == [
        {
            "op": 2,
            "d": {
                "token": "QQBot access-token",
                "intents": 1 << 25,
                "shard": [0, 1],
            },
        }
    ]


@pytest.mark.anyio
async def test_gateway_heartbeat_requires_an_ack():
    stream = object.__new__(QQStream)
    stream._sequence = None
    stream._heartbeat_ack = anyio.Event()
    socket = FakeSocket()
    with pytest.raises(TimeoutError):
        await stream._heartbeat(socket, 0.001)


async def _token(value):
    return value


@pytest.mark.anyio
async def test_gateway_ready_connects_and_dispatches_events(monkeypatch):
    adapter = make_adapter(enabled=False)
    plugin = FakePlugin("support")
    channel = declare(adapter, plugin)
    stream = object.__new__(QQStream)
    stream.channel = channel
    stream.adapter = adapter
    stream._session_id = None
    stream._sequence = None
    connected = []
    delivered = []
    monkeypatch.setattr(
        adapter, "stream_connected", lambda value: connected.append(value)
    )

    async def accept_event(*_args):
        delivered.append(True)

    monkeypatch.setattr(adapter, "accept_event", accept_event)
    await stream._dispatch(
        {
            "op": 0,
            "t": "READY",
            "d": {"session_id": "session-1"},
        }
    )
    await stream._dispatch(message_payload())
    assert connected == [channel]
    assert delivered == [True]
    assert stream._session_id == "session-1"


@pytest.mark.anyio
async def test_adapter_admits_gateway_event_once(monkeypatch):
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
        "opsmesh.plugins.channels.qq.adapter.dispatch_event", fake_dispatch
    )
    async with adapter.driver.lifespan():
        await adapter.startup()
        await adapter.accept_event(channel, message_payload())
        with anyio.fail_after(2):
            while not dispatched:
                await anyio.sleep(0)
        assert dispatched[0][1].get_plaintext() == "hello"
        await adapter.shutdown()
