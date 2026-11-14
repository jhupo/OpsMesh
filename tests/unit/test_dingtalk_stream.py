"""Exercise the SDK bridge with bounded, offline network transports."""

import json
from types import SimpleNamespace

import anyio
import httpx
import pytest
from dingtalk_stream import ChatbotMessage, SystemMessage

from opsmesh.plugins.channels.dingtalk.client import DingTalkClient
from opsmesh.plugins.channels.dingtalk.config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.dingtalk.errors import DingTalkProtocolError
from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream


class Socket:
    def __init__(self, frames=()):
        self.frames = list(frames)
        self.acks = []
        self.closed = False
        self.listening = anyio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.listening.set()
        if self.frames:
            return json.dumps(self.frames.pop(0))
        await anyio.sleep_forever()

    async def send(self, frame):
        self.acks.append(json.loads(frame))

    async def close(self):
        self.closed = True


def make_stream(http, **settings):
    protocol = DingTalkSettings(reconnect_delay=0.001, **settings)
    connections, disconnected, callbacks = [], [], []

    async def accept_callback(channel, data):
        callbacks.append(data)
        return 200, "ok"

    adapter = SimpleNamespace(
        client=DingTalkClient(protocol, http),
        stream_connected=connections.append,
        stream_disconnected=disconnected.append,
        stream_finished=lambda channel: None,
        accept_callback=accept_callback,
    )
    channel = SimpleNamespace(
        self_id="dingtalk:plugin:bot",
        adapter=adapter,
        settings=DingTalkConnectionSettings(
            client_id="test-client", client_secret_ref="TEST_SECRET"
        ),
        protocol=protocol,
    )
    return DingTalkStream(channel, "private-secret"), connections, callbacks


@pytest.mark.anyio
async def test_handshake_sdk_ack_reconnect_and_stop(monkeypatch):
    requests, uris = [], []

    async def negotiate(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "endpoint": "wss://example.test/stream",
                "ticket": "secret ticket",
            },
        )

    first = Socket(
        [
            {
                "type": "SYSTEM",
                "headers": {
                    "topic": SystemMessage.TOPIC_DISCONNECT,
                    "messageId": "disconnect-1",
                },
                "data": "{}",
            }
        ]
    )
    second = Socket(
        [
            {
                "type": "CALLBACK",
                "headers": {
                    "topic": ChatbotMessage.TOPIC,
                    "messageId": "callback-1",
                },
                "data": json.dumps({"text": {"content": "hello"}}),
            }
        ]
    )
    sockets = iter((first, second))

    async def connect(uri, **options):
        uris.append(uri)
        assert options["open_timeout"] == 0.2
        return next(sockets)

    monkeypatch.setattr(
        "opsmesh.plugins.channels.dingtalk.stream.connect", connect
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(negotiate)
    ) as http:
        stream, connections, callbacks = make_stream(http, request_timeout=0.2)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(stream.run)
            with anyio.fail_after(2):
                await second.listening.wait()
                stream.stop()
                await stream.wait_closed()
        assert len(connections) == 2
        assert first.closed and second.closed
        assert first.acks[0]["headers"]["messageId"] == "disconnect-1"
        assert second.acks[0]["headers"]["messageId"] == "callback-1"
        assert callbacks == [{"text": {"content": "hello"}}]
        assert all(
            body["subscriptions"]
            == [{"type": "CALLBACK", "topic": ChatbotMessage.TOPIC}]
            for body in requests
        )
        assert len(uris) == 2
        assert "ticket=secret+ticket" in uris[0]


@pytest.mark.anyio
async def test_cancellation_interrupts_pending_handshake():
    pending = anyio.Event()

    async def negotiate(request):
        pending.set()
        await anyio.sleep_forever()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(negotiate)
    ) as http:
        stream, connected, _ = make_stream(http)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(stream.run)
            await pending.wait()
            stream.stop()
            with anyio.fail_after(1):
                await stream.wait_closed()
        assert not connected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"endpoint": "ws://example.test", "ticket": "x"},
        {"endpoint": "wss://example.test", "ticket": ""},
    ],
)
async def test_invalid_connection_response_is_rejected(response):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        )
    ) as http:
        stream, _, _ = make_stream(http)
        with pytest.raises(DingTalkProtocolError):
            await stream._connection_uri()
