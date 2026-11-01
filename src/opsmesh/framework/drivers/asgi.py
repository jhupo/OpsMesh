"""Bind driver ingress to Starlette/FastAPI; no platform services or authentication policy."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket as ASGIWebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from opsmesh.framework.errors import OpsMeshError

from . import (
    HTTPRequest,
    HTTPServer,
    HTTPServerSetup,
    OpsMeshDriver,
    WebSocketServer,
    WebSocketServerConnection,
    WebSocketServerSetup,
)

logger = logging.getLogger(__name__)


class ASGIWebSocketConnection(WebSocketServerConnection):
    def __init__(self, connection: ASGIWebSocket) -> None:
        self.connection = connection

    async def accept(self, subprotocol: str | None = None) -> None:
        await self.connection.accept(subprotocol=subprotocol)

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, str):
            await self.connection.send_text(data)
        else:
            await self.connection.send_bytes(data)

    async def receive(self) -> str | bytes:
        if self.connection.application_state != WebSocketState.CONNECTED:
            raise RuntimeError("Accept the WebSocket before receiving frames")
        message = await self.connection.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        data = message.get("text")
        return str(data) if data is not None else bytes(message["bytes"])

    async def close(self, code: int = 1000) -> None:
        if (
            self.connection.application_state != WebSocketState.DISCONNECTED
            and self.connection.client_state != WebSocketState.DISCONNECTED
        ):
            await self.connection.close(code=code)


def _http_error(error: OpsMeshError) -> JSONResponse:
    statuses = {
        "PLUGIN_DISABLED": 403,
        "FORBIDDEN": 403,
        "UNAUTHORIZED": 401,
        "UNAUTHENTICATED": 401,
        "INVALID_REQUEST": 400,
        "INPUT_TOO_LARGE": 413,
    }
    status = statuses.get(error.code, 503 if error.retryable else 500)
    return JSONResponse(
        {"error": {"code": error.code, "message": "The driver request could not be completed"}},
        status_code=status,
    )


def _http_endpoint(setup: HTTPServerSetup) -> Callable[[Request], Awaitable[Response]]:
    async def handle(request: Request) -> Response:
        incoming = HTTPRequest(
            request.method, str(request.url), dict(request.headers), await request.body()
        )
        try:
            response = await setup.handler(incoming)
            return Response(response.content, response.status_code, headers=response.headers)
        except OpsMeshError as error:
            return _http_error(error)
        except Exception:
            logger.exception("Driver HTTP handler failed", extra={"path": setup.path})
            return _http_error(OpsMeshError("INTERNAL_ERROR", "Driver handler failed"))

    return handle


def _websocket_endpoint(
    setup: WebSocketServerSetup,
) -> Callable[[ASGIWebSocket], Awaitable[None]]:
    async def handle(socket: ASGIWebSocket) -> None:
        connection = ASGIWebSocketConnection(socket)
        request = HTTPRequest("GET", str(socket.url), dict(socket.headers))
        close_code = 1000
        try:
            await setup.handler(request, connection)
        except WebSocketDisconnect:
            pass
        except OpsMeshError:
            close_code = 1008
        except Exception:
            close_code = 1011
            logger.exception("Driver WebSocket handler failed", extra={"path": setup.path})
        finally:
            await connection.close(close_code)

    return handle


def _check_conflicts(existing: list[BaseRoute], candidate: Route | WebSocketRoute) -> None:
    for route in existing:
        if isinstance(route, Mount) and (
            candidate.path == route.path or candidate.path.startswith(route.path + "/")
        ):
            raise OpsMeshError("ROUTE_CONFLICT", "Driver route overlaps an existing mount")
        if (
            isinstance(candidate, Route)
            and isinstance(route, Route)
            and route.path_regex.fullmatch(candidate.path)
            and (
                route.methods is None
                or candidate.methods is None
                or route.methods & candidate.methods
            )
        ):
            raise OpsMeshError("ROUTE_CONFLICT", "Driver route overlaps an existing HTTP route")
        if (
            isinstance(candidate, WebSocketRoute)
            and isinstance(route, WebSocketRoute)
            and route.path_regex.fullmatch(candidate.path)
        ):
            raise OpsMeshError(
                "ROUTE_CONFLICT", "Driver route overlaps an existing WebSocket route"
            )


def mount_driver_routes(app: Starlette, driver: OpsMeshDriver) -> None:
    """Mount once after startup. The host owns body limits; handlers own protocol authentication.

    Declarations are frozen after mounting. Disabled plugins reject new calls and
    existing WebSocket I/O; disabling cannot undo work an HTTP handler already began.
    """
    mounted = getattr(app.state, "opsmesh_ingress_driver", None)
    if mounted is driver:
        return
    if mounted is not None:
        raise OpsMeshError("ROUTE_CONFLICT", "A different driver is already mounted")
    routes: list[BaseRoute] = []
    if isinstance(driver, HTTPServer):
        for setup in driver.http_server_setups:
            route = Route(setup.path, _http_endpoint(setup), methods=sorted(setup.methods))
            _check_conflicts([*app.router.routes, *routes], route)
            routes.append(route)
    if isinstance(driver, WebSocketServer):
        for ws_setup in driver.websocket_server_setups:
            ws_route = WebSocketRoute(ws_setup.path, _websocket_endpoint(ws_setup))
            _check_conflicts([*app.router.routes, *routes], ws_route)
            routes.append(ws_route)
    if isinstance(driver, (HTTPServer, WebSocketServer)):
        driver.freeze_server_routes()
    app.router.routes.extend(routes)
    app.state.opsmesh_ingress_driver = driver
