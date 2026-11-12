"""Verify native FastAPI serving and channel-owned listener cleanup."""

import json
import threading
import time
import urllib.request

import anyio
import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import Response
from websockets.asyncio.client import connect

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins.channels.api.streaming import ManagedStreamingResponse
from opsmesh.plugins.channels.server import ListenerSettings, Server


def new_server(**options):
    return Server(FastAPI(), ListenerSettings(**options))


@pytest.mark.anyio
async def test_listener_binds_an_ephemeral_port_and_drains_on_shutdown():
    server = new_server(enabled=True, host="127.0.0.1", port=0)
    host = Driver(Env(), Config())

    async def start_server():
        await server.startup(host.task_group)

    host.on_started(start_server)
    host.on_quiescing(server.shutdown)

    async def handler(request: Request):
        return Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps({"status": "ok"}),
        )

    server.app.add_api_route("/health", handler, methods=["GET"])
    async with host.lifespan():
        port = server.bound_port
        assert port is not None

        def fetch():
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5
            ) as response:
                return response.status, response.read()

        status, payload = await anyio.to_thread.run_sync(fetch)
        assert status == 200
        assert json.loads(payload) == {"status": "ok"}
    assert server.bound_port is None


@pytest.mark.anyio
async def test_websocket_listener_uses_channels_dependency_and_closes():
    server = new_server(enabled=True, host="127.0.0.1", port=0)
    host = Driver(Env(), Config())

    async def start_server():
        await server.startup(host.task_group)

    host.on_started(start_server)
    host.on_quiescing(server.shutdown)
    closed = anyio.Event()

    async def echo(websocket: WebSocket):
        await websocket.accept()
        try:
            await websocket.send_text(await websocket.receive_text())
        finally:
            await websocket.close()
            closed.set()

    server.app.add_api_websocket_route("/echo", echo)
    async with host.lifespan():
        address = f"ws://127.0.0.1:{server.bound_port}/echo"
        async with connect(address) as client:
            await client.send("hello")
            assert await client.recv() == "hello"
        with anyio.fail_after(5):
            await closed.wait()
    assert server.bound_port is None


@pytest.mark.anyio
async def test_listener_startup_failure_rolls_back_resources(monkeypatch):
    server = new_server(
        enabled=True, host="127.0.0.1", port=0, shutdown_timeout=0.2
    )
    host = Driver(Env(), Config())

    async def start_server():
        await server.startup(host.task_group)

    host.on_started(start_server)
    host.on_quiescing(server.shutdown)
    sockets = []
    original_bind = server._bind_socket

    def bind():
        sock = original_bind()
        sockets.append(sock)
        return sock

    monkeypatch.setattr(server, "_bind_socket", bind)
    opened = []
    host.on_startup(lambda: opened.append("open"))
    host.on_shutdown(lambda: opened.pop())

    async def fail_startup(self, sockets=None):
        raise OSError("address already in use")

    monkeypatch.setattr(uvicorn.Server, "startup", fail_startup)
    with pytest.raises(OSError, match="address already in use"):
        await host.startup()

    assert host.started is False
    assert server.bound_port is None
    assert opened == []
    assert sockets and sockets[0].fileno() == -1


@pytest.mark.anyio
async def test_shutdown_cancels_an_endless_stream_at_the_deadline():
    server = new_server(
        enabled=True, host="127.0.0.1", port=0, shutdown_timeout=0.3
    )
    host = Driver(Env(), Config())

    async def start_server():
        await server.startup(host.task_group)

    host.on_started(start_server)
    host.on_quiescing(server.shutdown)

    class EndlessStream:
        def __init__(self):
            self.sent = False
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return b"tick"
            await anyio.sleep_forever()

        async def aclose(self):
            self.closed += 1

    stream = EndlessStream()
    plugin_closed = []
    host.on_shutdown(lambda: plugin_closed.append("closed"))

    async def handler(request: Request):
        return ManagedStreamingResponse(
            stream, on_close=lambda error: stream.aclose()
        )

    server.app.add_api_route("/stream", handler, methods=["GET"])
    await host.startup()
    port = server.bound_port
    assert port is not None

    chunks = []
    errors = []
    release = threading.Event()

    def fetch():
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/stream", timeout=10
            ) as response:
                chunks.append(response.read(4))
                release.wait(10)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=fetch, daemon=True)
    thread.start()
    try:
        with anyio.fail_after(5):
            while not chunks and not errors:
                await anyio.sleep(0.02)
        assert chunks == [b"tick"]
        started = time.monotonic()
        await host.shutdown()
        assert time.monotonic() - started < 3
        assert server.bound_port is None
        assert plugin_closed == ["closed"]
        with anyio.fail_after(5):
            while stream.closed == 0:
                await anyio.sleep(0.02)
    finally:
        release.set()
        await anyio.to_thread.run_sync(thread.join, 5)

    assert errors == []
    assert stream.closed == 1
