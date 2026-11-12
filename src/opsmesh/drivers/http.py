"""ASGI HTTP transport owned by the process host.

This driver implements the framework transport interfaces only. It does not
know about plugins, authentication, agents, or sessions.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import anyio
import uvicorn
from anyio.abc import TaskStatus
from multidict import CIMultiDict

from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import (
    URL,
    ASGIMixin,
    Driver,
    HTTPServerSetup,
    Request,
    Response,
    ResponseStream,
    WebSocket,
    WebSocketServerSetup,
)

logger = logging.getLogger(__name__)

ASGIScope = Mapping[str, Any]
ASGIReceive = Callable[[], Awaitable[Mapping[str, Any]]]
ASGISend = Callable[[Mapping[str, Any]], Awaitable[None]]

_EMPTY_STATUSES = frozenset({204, 304})

_PARAM_SEGMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class _HTTPRoute:
    """One registered route, optionally matching a path template."""

    setup: HTTPServerSetup
    template: str
    skeleton: str
    regex: re.Pattern[str] | None


def _normalize_path(path: str) -> str:
    """Collapse duplicate separators and reject traversal segments."""
    if not path.startswith("/"):
        raise ValueError("HTTP route paths must start with '/'")
    if "?" in path or "#" in path:
        raise ValueError("HTTP route paths cannot contain a query or fragment")
    segments: list[str] = []
    for segment in path.split("/")[1:]:
        if not segment or segment == ".":
            continue
        if segment == "..":
            raise ValueError("HTTP route paths cannot traverse upward")
        if "\\" in segment or any(ord(char) < 0x20 for char in segment):
            raise ValueError("HTTP route paths contain an invalid character")
        segments.append(segment)
    return "/" + "/".join(segments)


def _compile_template(
    template: str,
) -> tuple[str, re.Pattern[str], tuple[str, ...]]:
    """Build a regex, skeleton, and parameter names for a route template."""
    segments = template.strip("/").split("/") if template != "/" else []
    parts: list[str] = []
    skeleton: list[str] = []
    names: list[str] = []
    for segment in segments:
        match = _PARAM_SEGMENT.match(segment)
        if match is not None:
            name = match.group(1)
            if name in names:
                raise ValueError(f"Duplicate path parameter: {name}")
            names.append(name)
            parts.append(f"(?P<{name}>[^/]+)")
            skeleton.append("{}")
            continue
        if "{" in segment or "}" in segment:
            raise ValueError("Path parameters must occupy a whole path segment")
        parts.append(re.escape(segment))
        skeleton.append(segment)
    pattern = "^/" + "/".join(parts) + "$" if parts else "^/$"
    return "/" + "/".join(skeleton), re.compile(pattern), tuple(names)


class RequestBodyTooLarge(ValueError):
    """The request body exceeded the transport limit."""


class _WebSocketDisconnected(Exception):
    """The peer disconnected while receiving a WebSocket message."""


class ASGIWebSocket(WebSocket):
    """Adapt one ASGI WebSocket connection to the framework contract."""

    def __init__(
        self,
        request: Request,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        """Wrap one accepted ASGI WebSocket channel.

        Args:
            request: The framework request for the handshake.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        super().__init__(request=request)
        self._receive = receive
        self._send = send
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the peer has disconnected or the channel was closed."""
        return self._closed

    async def accept(self) -> None:
        """Accept the WebSocket handshake."""
        if not self._closed:
            await self._send({"type": "websocket.accept"})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket channel once."""
        if self._closed:
            return
        self._closed = True
        await self._send(
            {"type": "websocket.close", "code": code, "reason": reason}
        )

    async def receive(self) -> str | bytes:
        """Receive one text or binary message."""
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            self._closed = True
            raise _WebSocketDisconnected
        if (text := message.get("text")) is not None:
            return text
        return message.get("bytes") or b""

    async def receive_text(self) -> str:
        """Receive one text message."""
        data = await self.receive()
        if isinstance(data, bytes):
            raise TypeError("WebSocket text message expected")
        return data

    async def receive_bytes(self) -> bytes:
        """Receive one binary message."""
        data = await self.receive()
        if isinstance(data, str):
            raise TypeError("WebSocket binary message expected")
        return data

    async def send_text(self, data: str) -> None:
        """Send one text message."""
        await self._send({"type": "websocket.send", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        """Send one binary message."""
        await self._send({"type": "websocket.send", "bytes": data})


class HTTPDriver(Driver, ASGIMixin):
    """Framework driver backed by an ASGI application and uvicorn server.

    Driver options are read from ``config.driver_options["http"]``:

    - host: Listen address; defaults to loopback.
    - port: Listen port; 0 selects an ephemeral port.
    - enabled: Whether to open a listener during startup.
    - access_log: Whether uvicorn logs access lines.
    - max_request_body: Buffered request body limit in bytes.
    - shutdown_timeout: Seconds to wait for active requests on shutdown.
    """

    def __init__(self, env: Env, config: Config):
        """Create a driver that owns one ASGI listener.

        Args:
            env: Runtime environment identity.
            config: Framework configuration and driver options.
        """
        super().__init__(env, config)
        options = dict(config.driver_options.get("http", {}))
        self.host: str = str(options.get("host", "127.0.0.1"))
        self.port: int = int(options.get("port", 8080))
        self.listen_enabled: bool = bool(options.get("enabled", True))
        self.access_log: bool = bool(options.get("access_log", False))
        self.max_request_body: int = int(
            options.get("max_request_body", 1024 * 1024)
        )
        self.shutdown_timeout: float = float(
            options.get("shutdown_timeout", 10.0)
        )
        if not 0 <= self.port <= 65535:
            raise ValueError("HTTP driver port must be between 0 and 65535")
        if self.max_request_body < 0:
            raise ValueError("HTTP driver body limit cannot be negative")
        if self.shutdown_timeout <= 0:
            raise ValueError("HTTP driver shutdown timeout must be positive")

        self._http_routes: dict[tuple[str, str], _HTTPRoute] = {}
        self._http_templates: dict[tuple[str, str], _HTTPRoute] = {}
        self._websocket_routes: dict[str, WebSocketServerSetup] = {}
        self._server: uvicorn.Server | None = None
        self._server_done: anyio.Event | None = None
        self._serve_scope: anyio.CancelScope | None = None
        self._socket: socket.socket | None = None
        self._listening = False

    @property
    def type(self) -> str:
        """The driver type name."""
        return "http"

    @property
    def logger(self) -> logging.Logger:
        """The driver-specific logger."""
        return logger

    @property
    def server_app(self) -> HTTPDriver:
        """The ASGI application object for this driver."""
        return self

    @property
    def asgi(self) -> HTTPDriver:
        """The ASGI callable for this driver."""
        return self

    @property
    def bound_port(self) -> int | None:
        """The port actually bound after startup, if listening."""
        if self._socket is None:
            return None
        return int(self._socket.getsockname()[1])

    def setup_http_server(self, setup: HTTPServerSetup) -> None:
        """Register one HTTP route before the listener opens.

        A path may contain full-segment parameters such as ``{name}``. A
        concrete route always wins over a template; two templates with the
        same shape are rejected as ambiguous.
        """
        if self._listening:
            raise RuntimeError("HTTP routes must be registered before startup")
        path = self._route_path(setup.path)
        method = setup.method.upper()
        if not method:
            raise ValueError("HTTP route method cannot be empty")
        skeleton, regex, names = _compile_template(path)
        is_template = bool(names)
        route = _HTTPRoute(
            setup, path, skeleton, regex if is_template else None
        )
        key = (method, path if not is_template else skeleton)
        if key in self._http_routes or key in self._http_templates:
            raise ValueError(f"Duplicate HTTP route {method} {path}")
        if is_template:
            self._http_templates[key] = route
        else:
            self._http_routes[key] = route

    def remove_http_server(self, method: str, path: str) -> bool:
        """Remove a route registered by setup_http_server."""
        method = method.upper()
        normalized = _normalize_path(path)
        skeleton, _, names = _compile_template(normalized)
        key = (method, skeleton if names else normalized)
        if key in self._http_routes:
            del self._http_routes[key]
            return True
        if key in self._http_templates:
            del self._http_templates[key]
            return True
        return False

    def setup_websocket_server(self, setup: WebSocketServerSetup) -> None:
        """Register one WebSocket route before the listener opens."""
        if self._listening:
            raise RuntimeError(
                "WebSocket routes must be registered before startup"
            )
        path = self._route_path(setup.path)
        if path in self._websocket_routes:
            raise ValueError(f"Duplicate WebSocket route {path}")
        self._websocket_routes[path] = setup

    async def startup(self) -> None:
        """Run host callbacks and then open the configured listener."""
        await super().startup()
        if not self.listen_enabled:
            return
        try:
            await self._start_listener()
        except BaseException:
            try:
                await self._stop_listener()
            except BaseException:
                logger.exception("Failed to clean up the HTTP listener")
            try:
                await super().shutdown()
            except BaseException:
                logger.exception(
                    "Failed to roll back host resources after a "
                    "listener failure"
                )
            raise

    async def shutdown(self) -> None:
        """Stop accepting requests, drain, then release host resources."""
        try:
            await self._stop_listener()
        finally:
            await super().shutdown()

    async def __call__(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        """Serve one ASGI HTTP, WebSocket, or lifespan scope."""
        scope_type = scope.get("type")
        if scope_type == "http":
            await self._handle_http(scope, receive, send)
        elif scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
        elif scope_type == "lifespan":
            return
        else:
            raise ValueError(f"Unsupported ASGI scope type: {scope_type!r}")

    async def _start_listener(self) -> None:
        sock = self._bind_socket()
        self._socket = sock
        server: uvicorn.Server | None = None
        try:
            config = uvicorn.Config(
                self,
                host=self.host,
                port=self.port,
                log_config=None,
                lifespan="off",
                access_log=self.access_log,
                proxy_headers=False,
                timeout_graceful_shutdown=self.shutdown_timeout,
            )
            server = uvicorn.Server(config)
            done = anyio.Event()
            self._server = server
            self._server_done = done
            # Server.startup expects the same preparation serve() performs.
            if not config.loaded:
                config.load()
            server.lifespan = config.lifespan_class(config)
            await server.startup(sockets=[sock])
            self._serve_scope = await self.task_group.start(
                self._serve, server, done
            )
        except BaseException:
            with anyio.CancelScope(shield=True):
                self._server = None
                self._server_done = None
                self._serve_scope = None
                self._socket = None
                if server is not None:
                    try:
                        with anyio.move_on_after(self.shutdown_timeout):
                            await server.shutdown(sockets=[sock])
                    except Exception:
                        logger.debug(
                            "HTTP listener startup cleanup failed",
                            exc_info=True,
                        )
                    await self._close_server_listeners(server)
                self._close_socket(sock)
            raise
        self._listening = True
        logger.info(
            "HTTP driver listening on %s:%s",
            self.host,
            self.bound_port,
        )

    async def _serve(
        self,
        server: uvicorn.Server,
        done: anyio.Event,
        *,
        task_status: TaskStatus[anyio.CancelScope] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        with anyio.CancelScope() as scope:
            task_status.started(scope)
            try:
                await server.main_loop()
            finally:
                done.set()

    def _bind_socket(self) -> socket.socket:
        addresses = socket.getaddrinfo(
            self.host,
            self.port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
        family, sock_type, protocol, _, address = addresses[0]
        sock = socket.socket(family, sock_type, protocol)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
            sock.listen(2048)
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        return sock

    async def _stop_listener(self) -> None:
        self._listening = False
        server, sock, done, serve_scope = (
            self._server,
            self._socket,
            self._server_done,
            self._serve_scope,
        )
        self._server = None
        self._socket = None
        self._server_done = None
        self._serve_scope = None
        try:
            if server is not None:
                await self._drain_server(server, sock, done)
        finally:
            if serve_scope is not None:
                serve_scope.cancel()
            self._close_socket(sock)

    async def _drain_server(
        self,
        server: uvicorn.Server,
        sock: socket.socket | None,
        done: anyio.Event | None,
    ) -> None:
        with anyio.CancelScope(shield=True):
            server.should_exit = True
            if done is not None:
                with anyio.move_on_after(self.shutdown_timeout) as stopping:
                    await done.wait()
                if stopping.cancelled_caught:
                    logger.warning(
                        "HTTP driver did not stop accepting requests within "
                        "%.1fs; forcing shutdown",
                        self.shutdown_timeout,
                    )
                    server.force_exit = True
            force = False
            try:
                with anyio.move_on_after(
                    self.shutdown_timeout + 1.0
                ) as draining:
                    await server.shutdown(
                        sockets=[sock] if sock is not None else None
                    )
                force = draining.cancelled_caught
            except Exception:
                logger.exception("Failed to close the HTTP listener")
                force = True
            if force:
                logger.error(
                    "HTTP driver could not drain active requests within "
                    "%.1fs; cancelling them",
                    self.shutdown_timeout,
                )
                await self._force_close(server)
            else:
                await self._wait_for_tasks(server)

    @staticmethod
    async def _wait_for_tasks(server: uvicorn.Server) -> None:
        pending = [
            task for task in list(server.server_state.tasks) if not task.done()
        ]
        if not pending:
            return
        with anyio.CancelScope(shield=True):
            try:
                with anyio.move_on_after(1.0):
                    await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                logger.debug(
                    "HTTP request tasks did not finish after shutdown",
                    exc_info=True,
                )

    async def _force_close(self, server: uvicorn.Server) -> None:
        server.should_exit = True
        server.force_exit = True
        for connection in list(server.server_state.connections):
            try:
                connection.shutdown()
            except Exception:
                logger.debug("HTTP connection shutdown failed", exc_info=True)
        for task in list(server.server_state.tasks):
            task.cancel()
        await self._close_server_listeners(server)
        await self._wait_for_tasks(server)

    @staticmethod
    async def _close_server_listeners(server: uvicorn.Server) -> None:
        for listener in list(getattr(server, "servers", ())):
            listener.close()
            try:
                with anyio.move_on_after(1.0):
                    await listener.wait_closed()
            except Exception:
                logger.debug("HTTP listener close failed", exc_info=True)

    @staticmethod
    def _close_socket(sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            logger.debug("HTTP listen socket close failed", exc_info=True)

    async def _handle_http(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        try:
            normalized = _normalize_path(path)
        except ValueError:
            await self._send_text(send, 404, "Not Found")
            return
        route, path_params, allowed = self._match_http(method, normalized)
        if route is None:
            if allowed is not None:
                await self._send_text(
                    send, 405, "Method Not Allowed", allow=allowed
                )
            else:
                await self._send_text(send, 404, "Not Found")
            return

        try:
            request = await self._build_request(scope, receive, path_params)
        except RequestBodyTooLarge:
            await self._send_text(send, 413, "Payload Too Large")
            return
        except ConnectionError:
            return
        except Exception:
            logger.exception("Failed to read an HTTP request body")
            await self._send_text(send, 400, "Bad Request")
            return

        async with anyio.create_task_group() as task_group:
            request_scope = anyio.CancelScope()

            async def watch_disconnect() -> None:
                try:
                    while True:
                        message = await receive()
                        if message.get("type") == "http.disconnect":
                            request_scope.cancel()
                            return
                except anyio.get_cancelled_exc_class():
                    raise
                except Exception:
                    return

            async def serve_request() -> None:
                try:
                    with request_scope:
                        await self._dispatch_http(
                            route, request, send, head=method == "HEAD"
                        )
                finally:
                    task_group.cancel_scope.cancel()

            task_group.start_soon(watch_disconnect)
            task_group.start_soon(serve_request)

    async def _dispatch_http(
        self,
        route: HTTPServerSetup,
        request: Request,
        send: ASGISend,
        *,
        head: bool,
    ) -> None:
        try:
            response = await route.handle_func(request)
            if not isinstance(response, Response):
                raise TypeError(
                    "HTTP route handler must return a framework Response"
                )
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:
            logger.exception(
                "HTTP route %s %s failed",
                request.method,
                request.url.path,
            )
            with anyio.CancelScope(shield=True):
                await self._send_text(send, 500, "Internal Server Error")
            return

        try:
            if isinstance(response.content, ResponseStream):
                await self._send_streamed(send, response, head=head)
            else:
                await self._send_buffered(send, response, head=head)
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:
            logger.debug(
                "HTTP response for %s %s could not be sent",
                request.method,
                request.url.path,
                exc_info=True,
            )

    async def _handle_websocket(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        path = str(scope.get("path", "/"))
        route = self._websocket_routes.get(path)
        if route is None:
            await send(
                {
                    "type": "websocket.close",
                    "code": 1000,
                    "reason": "Not Found",
                }
            )
            return

        request = self._build_websocket_request(scope)
        websocket = ASGIWebSocket(request, receive, send)
        try:
            await route.handle_func(websocket)
        except _WebSocketDisconnected:
            pass
        except Exception:
            logger.exception("WebSocket route %s failed", path)
        finally:
            if not websocket.closed:
                try:
                    await websocket.close(1011, "Internal Server Error")
                except Exception:
                    logger.debug("WebSocket close failed", exc_info=True)

    def _match_http(
        self, method: str, path: str
    ) -> tuple[
        HTTPServerSetup | None,
        dict[str, str],
        str | None,
    ]:
        route = self._http_routes.get((method, path))
        if route is not None:
            return route.setup, {}, None
        template = self._match_template(method, path)
        if template is not None:
            setup, params = template
            return setup, params, None
        if method == "HEAD":
            route = self._http_routes.get(("GET", path))
            if route is not None:
                return route.setup, {}, None
            template = self._match_template("GET", path)
            if template is not None:
                setup, params = template
                return setup, params, None
        allowed = self._allowed_methods(path)
        if allowed:
            return None, {}, allowed
        return None, {}, None

    def _match_template(
        self, method: str, path: str
    ) -> tuple[HTTPServerSetup, dict[str, str]] | None:
        for (candidate_method, _), route in self._http_templates.items():
            if candidate_method != method or route.regex is None:
                continue
            match = route.regex.fullmatch(path)
            if match is not None:
                return route.setup, match.groupdict()
        return None

    def _allowed_methods(self, path: str) -> str | None:
        methods = {
            method
            for method, candidate in self._http_routes
            if candidate == path
        }
        methods.update(
            method
            for (method, _), route in self._http_templates.items()
            if route.regex is not None and route.regex.fullmatch(path)
        )
        if not methods:
            return None
        allowed = sorted(methods)
        if "GET" in allowed:
            allowed.insert(allowed.index("GET") + 1, "HEAD")
        return ", ".join(dict.fromkeys(allowed))

    async def _build_request(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        path_params: Mapping[str, str] | None = None,
    ) -> Request:
        body = await self._read_body(receive)
        headers = self._scope_headers(scope)
        return Request(
            str(scope.get("method", "GET")),
            self._scope_url(scope),
            headers=headers,
            cookies=self._scope_cookies(headers),
            content=body,
            path_params=path_params,
        )

    def _build_websocket_request(self, scope: ASGIScope) -> Request:
        return Request(
            "GET",
            self._scope_url(scope),
            headers=self._scope_headers(scope),
        )

    async def _read_body(self, receive: ASGIReceive) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                raise ConnectionError("Client disconnected")
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_request_body:
                raise RequestBodyTooLarge
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _scope_headers(scope: ASGIScope) -> CIMultiDict[str]:
        return CIMultiDict(
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in scope.get("headers", ())
        )

    @staticmethod
    def _scope_url(scope: ASGIScope) -> URL:
        server = scope.get("server") or ("localhost", 80)
        return URL.build(
            scheme=str(scope.get("scheme", "http")),
            host=str(server[0]),
            port=int(server[1]) if server[1] is not None else None,
            path=str(scope.get("path", "/")),
            query_string=bytes(scope.get("query_string", b"")).decode(
                "latin-1"
            ),
        )

    @staticmethod
    def _scope_cookies(headers: CIMultiDict[str]) -> list[tuple[str, str]]:
        cookies: list[tuple[str, str]] = []
        for raw in headers.getall("cookie", []):
            for item in raw.split(";"):
                name, separator, value = item.strip().partition("=")
                if separator and name:
                    cookies.append((name, value))
        return cookies

    async def _send_buffered(
        self, send: ASGISend, response: Response, *, head: bool
    ) -> None:
        content = response.content
        if not isinstance(content, (str, bytes)) and content is not None:
            raise TypeError("Buffered response content must be str or bytes")
        headers = self._response_headers(response, content)
        body = self._encode_body(content, response.status_code)
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"" if head else body,
                "more_body": False,
            }
        )

    async def _send_streamed(
        self, send: ASGISend, response: Response, *, head: bool
    ) -> None:
        stream = response.content
        assert isinstance(stream, ResponseStream)
        headers = self._response_headers(response, None)
        started = False
        failure: BaseException | None = None
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": headers,
                }
            )
            started = True
            if not head and response.status_code not in _EMPTY_STATUSES:
                async for chunk in stream:
                    if not isinstance(chunk, bytes):
                        raise TypeError(
                            "Streamed response chunks must be bytes"
                        )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
        except anyio.get_cancelled_exc_class() as error:
            failure = error
            raise
        except Exception as error:
            failure = error
            logger.exception("HTTP response stream failed")
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(self.shutdown_timeout):
                        await stream.aclose(failure)
                except Exception:
                    logger.exception("HTTP response stream close failed")
                if started:
                    try:
                        await send(
                            {
                                "type": "http.response.body",
                                "body": b"",
                                "more_body": False,
                            }
                        )
                    except Exception:
                        logger.debug("HTTP response stream close failed")

    @staticmethod
    def _response_headers(
        response: Response, content: str | bytes | None
    ) -> list[tuple[bytes, bytes]]:
        headers = CIMultiDict(response.headers)
        if isinstance(content, str) and "content-type" not in headers:
            headers["content-type"] = "text/plain; charset=utf-8"
        return [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ]

    @staticmethod
    def _encode_body(content: str | bytes | None, status: int) -> bytes:
        if status in _EMPTY_STATUSES or content is None:
            return b""
        if isinstance(content, str):
            return content.encode("utf-8")
        return content

    @staticmethod
    async def _send_text(
        send: ASGISend, status: int, message: str, *, allow: str | None = None
    ) -> None:
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(message.encode("utf-8"))).encode()),
        ]
        if allow is not None:
            headers.append((b"allow", allow.encode("latin-1")))
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": message.encode("utf-8"),
                "more_body": False,
            }
        )

    @staticmethod
    def _route_path(url: URL) -> str:
        if url.scheme or url.host or url.query_string or url.fragment:
            raise ValueError("HTTP route paths must be relative URL paths")
        return _normalize_path(url.path)


async def iterate_bytes(
    generator: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Adapt a plain async iterator for use as a response stream."""
    async for chunk in generator:
        yield chunk
