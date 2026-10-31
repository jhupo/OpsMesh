"""Real ASGI HTTP/WS requests; no listening sockets or external protocol services."""

from contextlib import asynccontextmanager
from textwrap import dedent

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from opsmesh.framework import init
from opsmesh.framework.drivers import (
    HTTPResponse,
    HTTPServer,
    HTTPServerSetup,
    OpsMeshNetworkDriver,
    WebSocketServer,
    WebSocketServerSetup,
)
from opsmesh.framework.drivers.asgi import mount_driver_routes
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import get_current_plugin, manager


def server_app(driver):
    @asynccontextmanager
    async def lifespan(app):
        async with driver.lifespan():
            mount_driver_routes(app, driver)
            yield

    return Starlette(lifespan=lifespan)


def load_server_plugin(tmp_path, monkeypatch, *, fail=False):
    source = dedent("""
        from opsmesh.framework import get_driver
        from opsmesh.framework.drivers import HTTPServerSetup, HTTPResponse, WebSocketServerSetup
        from opsmesh.framework.plugin import get_current_plugin

        owner = get_current_plugin()
        requests = []

        async def http(request):
            if request.headers.get("x-adapter-key") != "signed":
                return HTTPResponse(401, {}, b"invalid protocol signature")
            requests.append(request)
            return HTTPResponse(202, {"x-adapter": "example"}, request.content)

        async def websocket(request, socket):
            if request.headers.get("x-adapter-key") != "signed":
                await socket.close(1008)
                return
            await socket.accept()
            while True:
                await socket.send(await socket.receive())

        get_driver().setup_http_server(HTTPServerSetup("/plugins/echo", {"POST"}, http))
        get_driver().setup_websocket_server(WebSocketServerSetup("/plugins/echo", websocket))

        @get_driver().on_startup
        async def startup_route():
            assert get_current_plugin() is owner
            get_driver().setup_http_server(HTTPServerSetup("/plugins/startup", {"POST"}, http))
    """)
    if fail:
        source += '\nraise RuntimeError("failed after route registration")\n'
    (tmp_path / "server_plugin.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return manager.load("server_plugin")


def test_owned_ingress_requests_upgrade_auth_disable_and_freeze(tmp_path, monkeypatch):
    driver = OpsMeshNetworkDriver()
    init(driver)
    assert isinstance(driver, HTTPServer) and isinstance(driver, WebSocketServer)
    plugin = load_server_plugin(tmp_path, monkeypatch)
    app = server_app(driver)
    headers = {"x-adapter-key": "signed"}
    with TestClient(app) as client:
        assert get_current_plugin() is None
        assert client.post("/plugins/echo", content=b"no key").status_code == 401
        response = client.post("/plugins/echo?format=raw", content=b"\xffraw", headers=headers)
        assert response.status_code == 202 and response.content == b"\xffraw"
        assert response.headers["x-adapter"] == "example"
        assert plugin.module.requests[0].url.endswith("?format=raw")
        assert (
            client.post("/plugins/startup", content=b"startup", headers=headers).status_code == 202
        )
        with (
            pytest.raises(WebSocketDisconnect) as rejected,
            client.websocket_connect("/plugins/echo"),
        ):
            pytest.fail("Unauthenticated WebSocket was accepted")
        assert rejected.value.code == 1008
        with client.websocket_connect("/plugins/echo", headers=headers) as websocket:
            websocket.send_text("hello")
            assert websocket.receive_text() == "hello"
            websocket.send_bytes(b"binary")
            assert websocket.receive_bytes() == b"binary"
            manager.disable(plugin.module_name)
            websocket.send_text("after-disable")
            with pytest.raises(WebSocketDisconnect) as disabled:
                websocket.receive_text()
            assert disabled.value.code == 1008
        assert client.post("/plugins/echo", headers=headers).status_code == 403
        assert client.post("/plugins/startup", headers=headers).status_code == 403
        with (
            pytest.raises(WebSocketDisconnect) as disabled_upgrade,
            client.websocket_connect("/plugins/echo", headers=headers),
        ):
            pytest.fail("Disabled plugin accepted an upgrade")
        assert disabled_upgrade.value.code == 1008
        manager.enable(plugin.module_name)
        assert client.post("/plugins/echo", headers=headers).status_code == 202
        with pytest.raises(OpsMeshError) as frozen:
            driver.setup_http_server(HTTPServerSetup("/plugins/late", {"POST"}, plugin.module.http))
        assert frozen.value.code == "REGISTRY_FROZEN"


def test_import_failure_rolls_back_ingress_and_startup_hook(tmp_path, monkeypatch):
    driver = OpsMeshNetworkDriver()
    init(driver)
    with pytest.raises(RuntimeError, match="failed after route"):
        load_server_plugin(tmp_path, monkeypatch, fail=True)
    assert not driver.http_server_setups and not driver.websocket_server_setups
    with TestClient(server_app(driver)) as client:
        assert client.post("/plugins/echo").status_code == 404
        assert client.post("/plugins/startup").status_code == 404
    assert get_current_plugin() is None


async def echo_http(request):
    return HTTPResponse(200, {}, b"echo")


async def echo_websocket(request, socket):
    await socket.accept()
    while True:
        await socket.send(await socket.receive())


def test_server_registration_conflicts_and_existing_host_routes():
    driver = OpsMeshNetworkDriver()
    driver.setup_http_server(HTTPServerSetup("/plugins/echo", {"GET"}, echo_http))
    with pytest.raises(OpsMeshError) as duplicate:
        driver.setup_http_server(HTTPServerSetup("/plugins/echo", {"HEAD"}, echo_http))
    assert duplicate.value.code == "ROUTE_CONFLICT"
    driver.setup_http_server(HTTPServerSetup("/plugins/echo", {"POST"}, echo_http))
    driver.setup_websocket_server(WebSocketServerSetup("/plugins/echo", echo_websocket))
    with pytest.raises(OpsMeshError) as duplicate_ws:
        driver.setup_websocket_server(WebSocketServerSetup("/plugins/echo", echo_websocket))
    assert duplicate_ws.value.code == "ROUTE_CONFLICT"

    async def host_route(request):
        return PlainTextResponse("host")

    app = Starlette(routes=[Route("/plugins/{path:path}", host_route, methods=["POST"])])
    with pytest.raises(OpsMeshError) as host_conflict:
        mount_driver_routes(app, driver)
    assert host_conflict.value.code == "ROUTE_CONFLICT"
    assert len(app.routes) == 1  # Validate the whole table before mutating the application.


@pytest.mark.parametrize(
    "path",
    ["/api/hooks", "/docs", "/openapi.json", "/health", "/metrics/x", "/", "/a/../api", "/{x}"],
)
def test_reserved_or_ambiguous_paths_are_rejected(path):
    with pytest.raises(ValueError):
        HTTPServerSetup(path, {"POST"}, echo_http)
    with pytest.raises(ValueError):
        WebSocketServerSetup(path, echo_websocket)


def test_driver_shutdown_closes_inbound_websockets():
    driver = OpsMeshNetworkDriver()
    driver.setup_websocket_server(WebSocketServerSetup("/plugins/echo", echo_websocket))
    with (
        TestClient(server_app(driver)) as client,
        client.websocket_connect("/plugins/echo") as websocket,
    ):
        websocket.send_text("connected")
        assert websocket.receive_text() == "connected"
        client.portal.call(driver.stop)
        with pytest.raises(WebSocketDisconnect) as stopped:
            websocket.receive_text()
        assert stopped.value.code == 1001
