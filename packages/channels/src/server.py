"""Channels-owned HTTP listener and connection lifecycle."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING

import anyio
from anyio.abc import TaskGroup, TaskStatus
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp

if TYPE_CHECKING:
    import uvicorn

logger = logging.getLogger(__name__)


class ListenerSettings(BaseModel):
    """Network limits inherited by each listening channel's settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    enabled: bool = True
    access_log: bool = False
    max_request_body: int = Field(default=1024 * 1024, ge=0)
    shutdown_timeout: float = Field(default=10.0, gt=0)


class Server:
    """Own the listener while the framework owns the process task group."""

    def __init__(self, app: ASGIApp, settings: ListenerSettings) -> None:
        """Prepare protocol state without opening a socket."""
        self.host = settings.host
        self.port = settings.port
        self.listen_enabled = settings.enabled
        self.access_log = settings.access_log
        self.shutdown_timeout = settings.shutdown_timeout
        self.app = app
        self._server: uvicorn.Server | None = None
        self._server_done: anyio.Event | None = None
        self._serve_scope: anyio.CancelScope | None = None
        self._socket: socket.socket | None = None
        self._listening = False

    async def startup(self, task_group: TaskGroup) -> None:
        """Listen after all plugin startup and readiness callbacks finish."""
        if self.listen_enabled and not self._listening:
            await self._start_listener(task_group)

    def stop_accepting(self) -> None:
        """Close listeners while preserving admitted request resources."""
        self._listening = False
        if self._server is not None:
            self._server.should_exit = True
            for listener in getattr(self._server, "servers", ()):
                listener.close()

    async def shutdown(self) -> None:
        """Drain HTTP requests before dependent plugins release resources."""
        self.stop_accepting()
        await self._stop_listener()

    @property
    def bound_port(self) -> int | None:
        """The port actually bound after startup, if listening."""
        if self._socket is None:
            return None
        return int(self._socket.getsockname()[1])

    async def _start_listener(self, task_group: TaskGroup) -> None:
        import uvicorn

        sock = self._bind_socket()
        self._socket = sock
        server: uvicorn.Server | None = None
        try:
            config = uvicorn.Config(
                self.app,
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
            self._serve_scope = await task_group.start(
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
            "Channel server listening on %s:%s",
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
                        "Channel server did not stop accepting requests within "
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
                    "Channel server could not drain active requests within "
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
