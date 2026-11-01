"""QQ Gateway tests use controlled local peers and never contact the QQ service."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import serve

from opsmesh.framework.adapters import BotConnectionConfiguration
from opsmesh.framework.drivers import OpsMeshDriver, WebSocket
from opsmesh.framework.drivers.websockets import WebSocketDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq import QQAdapter, QQBotSettings
from opsmesh.infrastructure.bots.qq.configuration import trusted_gateway
from opsmesh.infrastructure.bots.qq.gateway import QQGatewaySession, QQGatewayState


def configuration(**settings: Any) -> BotConnectionConfiguration:
    return BotConnectionConfiguration(
        id="gateway-qq",
        adapter="qq",
        self_id="90001",
        settings={
            "app_id": "12345",
            "transport": "websocket",
            "reconnect_seconds": 0.05,
            "api_base_url": "http://127.0.0.1:8000",
            **settings,
        },
        credentials={"client_secret": "env:QQ_CLIENT_SECRET"},
    )


def dispatch(sequence: int = 2) -> dict[str, Any]:
    return {
        "op": 0,
        "t": "GROUP_MESSAGE_CREATE",
        "s": sequence,
        "d": {
            "id": f"message-{sequence}",
            "author": {"member_openid": "member1"},
            "group_openid": "group1",
            "content": "hello",
            "timestamp": "2026-11-01T00:00:00Z",
            "message_scene": {"ext": [f"msg_idx={sequence}", "auth_token=secret-provider-token"]},
        },
    }


def ready() -> dict[str, Any]:
    return {
        "op": 0,
        "t": "READY",
        "s": 1,
        "d": {
            "session_id": "session-private",
            "user": {"id": "90001"},
        },
    }


class ControlledSocket(WebSocket):
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed = False

    async def send(self, data: str | bytes) -> None:
        await self.outgoing.put(json.loads(data))

    async def receive(self) -> str | bytes:
        return await self.incoming.get()

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    async def feed(self, frame: dict[str, Any]) -> None:
        await self.incoming.put(json.dumps(frame))


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://evil.invalid",
        "wss://api.bot.qq.com.evil.invalid",
        "ws://api.bot.qq.com",
        "wss://user@api.bot.qq.com",
        "wss://api.bot.qq.com?token=x",
        "ws://127.0.0.1:9999",
    ],
)
def test_gateway_never_forwards_credentials_to_untrusted_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError):
        trusted_gateway(endpoint, QQBotSettings(app_id="12345"))


def test_gateway_official_hosts_and_explicit_test_peers() -> None:
    settings = QQBotSettings(app_id="12345")
    assert trusted_gateway("wss://api.sgroup.qq.com/websocket", settings).startswith("wss:")
    settings = QQBotSettings.model_validate(configuration().settings)
    assert trusted_gateway("ws://127.0.0.1:9999/gateway", settings).startswith("ws:")


async def test_websocket_requires_capable_driver_but_read_only_configuration_is_allowed() -> None:
    async with httpx.AsyncClient() as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: "unused")
        with pytest.raises(OpsMeshError) as caught:
            adapter.validate_configuration(configuration())
        assert caught.value.code == "CONFIG_INVALID"
        readonly = QQAdapter(OpsMeshDriver(), client, lambda ref: "unused", autostart=False)
        readonly.configure(configuration())
        assert readonly.connection_status(configuration()).state == "configured"
        await readonly.close()


async def test_blocked_consumer_does_not_block_heartbeat_or_commit_unaccepted_event() -> None:
    socket, state = ControlledSocket(), QQGatewayState()
    started = asyncio.Event()

    async def consume(config, event) -> None:
        started.set()
        await asyncio.Future()

    config = configuration()
    session = QQGatewaySession(
        socket, config, QQBotSettings.model_validate(config.settings), state, consume
    )
    await socket.feed({"op": 10, "d": {"heartbeat_interval": 40}})
    task = asyncio.create_task(session.run("private-token"))
    try:
        identify = await asyncio.wait_for(socket.outgoing.get(), 1)
        assert identify["op"] == 2 and identify["d"]["intents"] == 1 << 25
        await socket.feed(ready())
        await socket.feed(dispatch())
        await asyncio.wait_for(started.wait(), 1)
        for _ in range(2):
            heartbeat = await asyncio.wait_for(socket.outgoing.get(), 1)
            assert heartbeat == {"op": 1, "d": 2}
            await socket.feed({"op": 11})
        assert state.sequence == 1
        await socket.feed({"op": 7})
        await asyncio.wait_for(task, 1)
        assert state.sequence == 1 and socket.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["missing-ack", "queue-overflow", "invalid-session"])
async def test_gateway_failure_preserves_only_accepted_sequence(failure: str) -> None:
    socket, state = ControlledSocket(), QQGatewayState("session-private", 4, "90001")
    started = asyncio.Event()

    async def consume(config, event) -> None:
        started.set()
        await asyncio.Future()

    config = configuration(callback_queue_size=1)
    session = QQGatewaySession(
        socket, config, QQBotSettings.model_validate(config.settings), state, consume
    )
    await socket.feed({"op": 10, "d": {"heartbeat_interval": 30}})
    task = asyncio.create_task(session.run("private-token"))
    try:
        resume = await asyncio.wait_for(socket.outgoing.get(), 1)
        assert resume["op"] == 6 and resume["d"]["seq"] == 4
        if failure == "queue-overflow":
            await socket.feed(dispatch(5))
            await asyncio.wait_for(started.wait(), 1)
            await socket.feed(dispatch(6))
            await socket.feed(dispatch(7))
        elif failure == "invalid-session":
            await socket.feed({"op": 9, "d": False})
        if failure == "invalid-session":
            await asyncio.wait_for(task, 1)
            assert state.session_id is None and state.sequence is None
        else:
            with pytest.raises(OpsMeshError) as caught:
                await asyncio.wait_for(task, 1)
            assert caught.value.code == "BOT_UNAVAILABLE" and state.sequence == 4
        assert socket.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_local_gateway_reconnects_with_resume_and_driver_stops_every_task(caplog) -> None:
    accepted, resumed, heartbeat_seen = asyncio.Event(), asyncio.Event(), asyncio.Event()
    handshakes, events, requests, errors = [], [], [], []

    async def consumer(config, incoming) -> None:
        events.append(incoming)
        accepted.set()

    async def peer(socket) -> None:
        try:
            await socket.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 50}}))
            handshake = json.loads(await socket.recv())
            handshakes.append(handshake)
            if len(handshakes) == 1:
                assert handshake["op"] == 2
                assert handshake["d"]["token"] == "QQBot private-access-token"
                await socket.send(json.dumps(ready()))
                await socket.send(json.dumps(dispatch()))
                await asyncio.wait_for(accepted.wait(), 1)
                await socket.send(json.dumps({"op": 7}))
            else:
                assert handshake["op"] == 6 and handshake["d"]["seq"] == 2
                await socket.send(json.dumps({"op": 0, "t": "RESUMED", "s": 3, "d": {}}))
                resumed.set()
                async for raw in socket:
                    frame = json.loads(raw)
                    assert frame == {"op": 1, "d": 3}
                    await socket.send(json.dumps({"op": 11}))
                    heartbeat_seen.set()
        except Exception as error:
            errors.append(error)

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        def http_peer(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path.endswith("getAppAccessToken"):
                return httpx.Response(
                    200, json={"access_token": "private-access-token", "expires_in": 7200}
                )
            assert request.method == "GET" and request.url.path == "/gateway"
            return httpx.Response(200, json={"url": f"ws://127.0.0.1:{port}/gateway"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(http_peer)) as client:
            driver = WebSocketDriver()
            adapter = QQAdapter(driver, client, lambda ref: "private-app-secret")
            adapter.event_consumer = consumer
            config = configuration()
            async with driver.lifespan():
                adapter.configure(config)
                await asyncio.wait_for(resumed.wait(), 3)
                await asyncio.wait_for(heartbeat_seen.wait(), 2)
                assert adapter.connection_status(config).state == "connected"
                assert len(events) == 1
                assert "secret-provider-token" not in json.dumps(events[0].payload)
                assert requests.count("/gateway") == 1
            assert all(task.done() for task in adapter.tasks.values())
            assert all(connection.session is None for connection in adapter.connections.values())
            await adapter.close()
            assert not adapter.tasks and not adapter.connections and not adapter.bots
            assert not driver.task_errors and not client.is_closed
    assert not errors
    assert "private-access-token" not in caplog.text and "private-app-secret" not in caplog.text


async def test_gateway_uses_configured_group_member_identity_for_self_mentions() -> None:
    socket, state = ControlledSocket(), QQGatewayState()
    accepted, events = asyncio.Event(), []

    async def consume(config, incoming) -> None:
        events.append(incoming.event)
        accepted.set()

    config = configuration(group_member_openids={"group1": "memberBot"})
    session = QQGatewaySession(
        socket, config, QQBotSettings.model_validate(config.settings), state, consume
    )
    await socket.feed({"op": 10, "d": {"heartbeat_interval": 1000}})
    task = asyncio.create_task(session.run("private-token"))
    try:
        await asyncio.wait_for(socket.outgoing.get(), 1)
        await socket.feed(ready())
        message = dispatch()
        message["d"].update(
            {"content": "hello <@memberBot>", "mentions": [{"member_openid": "memberBot"}]}
        )
        await socket.feed(message)
        await asyncio.wait_for(accepted.wait(), 1)
        assert events[0].to_me is True and state.bot_user_id == "90001"
        await socket.feed({"op": 7})
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
