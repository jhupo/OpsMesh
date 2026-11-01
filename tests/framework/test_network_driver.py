from contextlib import asynccontextmanager

import httpx
import pytest

from opsmesh.framework import get_driver, init
from opsmesh.framework.adapters import OpsMeshAdapter
from opsmesh.framework.drivers import (
    DriverState,
    HTTPClient,
    HTTPRequest,
    OpsMeshNetworkDriver,
    WebSocketClient,
)
from opsmesh.framework.drivers import websockets as websocket_module


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.closed = 0

    async def handle_async_request(self, request):
        return httpx.Response(200, content=b"from-http")

    async def aclose(self):
        self.closed += 1


class WebSocketPeer:
    def __init__(self):
        self.frames = []
        self.closed = False

    async def send(self, data):
        self.frames.append(data)

    async def recv(self):
        return "from-websocket"

    async def close(self, code=1000):
        self.closed = True


async def test_combined_driver_has_both_capabilities_and_one_lifecycle(monkeypatch):
    transport, peer = CountingTransport(), WebSocketPeer()
    calls = []

    @asynccontextmanager
    async def connect(url, additional_headers=None):
        assert url == "ws://local.test" and additional_headers == {"X-Example": "one"}
        calls.append("connect")
        try:
            yield peer
        finally:
            await peer.close()
            calls.append("disconnect")

    monkeypatch.setattr(websocket_module, "connect", connect)
    driver = OpsMeshNetworkDriver(transport=transport)
    init(driver)
    assert get_driver() is driver
    assert isinstance(driver, HTTPClient) and isinstance(driver, WebSocketClient)
    assert driver.capabilities == frozenset(
        {"http_client", "websocket_client", "http_server", "websocket_server"}
    )

    class BothCapabilities(OpsMeshAdapter):
        required_capabilities = frozenset({"http_client", "websocket_client"})

    assert driver.register_adapter(BothCapabilities).driver is driver

    @driver.on_startup
    async def startup():
        calls.append("startup")

    @driver.on_shutdown
    async def shutdown():
        calls.append("shutdown")

    async with driver.lifespan():
        assert (
            await driver.request(HTTPRequest("GET", "https://local.test"))
        ).content == b"from-http"
        async with driver.websocket("ws://local.test", headers={"X-Example": "one"}) as socket:
            await socket.send("hello")
            assert await socket.receive() == "from-websocket"
            await driver.stop()
            assert peer.closed  # Even a caller-held connection closes with the driver.
        assert driver.state == DriverState.STOPPED
    assert transport.closed == 1
    assert calls.count("startup") == calls.count("shutdown") == 1
    assert calls.count("connect") == calls.count("disconnect") == 1
    assert peer.frames == ["hello"]
    with pytest.raises(RuntimeError, match="not running"):
        async with driver.websocket("ws://local.test"):
            pytest.fail("Stopped driver accepted a connection")


async def test_combined_driver_start_failure_closes_http_transport_once():
    transport = CountingTransport()
    driver = OpsMeshNetworkDriver(transport=transport)

    @driver.on_startup
    async def fail():
        raise RuntimeError("Plugin startup failed")

    with pytest.raises(RuntimeError, match="Plugin startup failed"):
        await driver.start()
    await driver.stop()
    assert transport.closed == 1
    assert driver.state == DriverState.STOPPED
