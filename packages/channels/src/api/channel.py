"""Own one consumer-configured HTTP message listener."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anyio
from fastapi import FastAPI, Request
from starlette.requests import ClientDisconnect
from starlette.responses import Response
from starlette.routing import BaseRoute

from ..channel import Channel
from .attachments import AttachmentUploads
from .attachments import mount as mount_attachments
from .codec import error_response, json_response
from .config import APIConnectionSettings
from .errors import HTTPError, request_error
from .exchange import MessageExchange
from .server import Server

if TYPE_CHECKING:
    from .adapter import APIAdapter


class APIChannel(Channel):
    """Dispatch fixed HTTP message endpoints to framework matchers."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types | {"data", "markdown", "table"}
    stream_formats = frozenset({"text", "markdown"})

    def __init__(
        self,
        adapter: APIAdapter,
        *,
        owner: Any,
        connection_name: str,
        settings: APIConnectionSettings,
    ) -> None:
        """Create an app and listener without opening a socket."""
        super().__init__(
            adapter,
            owner=owner,
            connection_name=connection_name,
            settings=settings,
        )
        self.uploads = AttachmentUploads(settings.attachments)
        self.app = FastAPI(
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            redirect_slashes=False,
        )
        self.app.add_exception_handler(HTTPError, self._http_error)
        self.server = Server(self.app, settings)
        self.mounted: dict[tuple[str, str], BaseRoute] = {}
        self._active_conversations: set[str] = set()
        self._conversations_drained = anyio.Event()
        self._conversations_drained.set()

    async def prepare(self) -> None:
        """Mount fixed routes so consumers can configure the app first."""
        self._mount_internal_routes()
        mount_attachments(self)

    async def start(self) -> None:
        """Listen after every consumer readiness callback has finished."""
        if not self.settings.enabled:
            self.mark_disconnected("connection is disabled")
            return
        await self.server.startup(self.adapter.driver.task_group)
        self._mark_online()

    def stop(self) -> None:
        """Close listeners while admitted requests keep their resources."""
        self.server.stop_accepting()
        self._mark_offline()

    async def drain(self) -> None:
        """Finish active requests before dependent plugins close."""
        await self.server.shutdown()
        with anyio.fail_after(self.settings.shutdown_timeout):
            await self._conversations_drained.wait()

    async def _release(self) -> None:
        """Remove this connection's routes and discard uploads."""
        self._unmount_all()
        self.uploads.clear()

    def reserve_conversation(self, key: str) -> None:
        """Reject concurrent requests for the same conversation window."""
        if key in self._active_conversations:
            raise HTTPError(409, "conversation is busy")
        if not self._active_conversations:
            self._conversations_drained = anyio.Event()
        self._active_conversations.add(key)

    def release_conversation(self, key: str) -> None:
        """Allow the next request for a conversation window."""
        self._active_conversations.discard(key)
        if not self._active_conversations:
            self._conversations_drained.set()

    async def _http_error(self, request: Request, error: HTTPError) -> Response:
        return error_response(error)

    def _mount_internal_routes(self) -> None:

        async def health(request: Request) -> Response:
            return json_response({"status": "ok"})

        async def ready(request: Request) -> Response:
            is_ready = self.ready and self.owner_ready
            payload: dict[str, Any] = {
                "status": "ready" if is_ready else "not_ready",
                "plugin": self.owner_id,
            }
            return json_response(payload, status_code=200 if is_ready else 503)

        for method, path, name, handler in (
            ("GET", "/healthz", "api.health", health),
            ("GET", "/readyz", "api.ready", ready),
            (
                "POST",
                "/plugins/{plugin_id}/messages",
                "api.messages",
                self._handle,
            ),
        ):
            self.app.add_api_route(
                path,
                handler,
                methods=[method],
                name=name,
                response_model=None,
            )
            self.mounted[(method, path)] = self.app.router.routes[-1]

    def _unmount_all(self) -> None:
        for route in tuple(self.mounted.values()):
            self.app.router.routes.remove(route)
        self.mounted.clear()

    async def _handle(self, request: Request) -> Response:
        """Admit one fixed message endpoint and dispatch its matchers."""
        try:
            self.check_request(request)
            return await MessageExchange(self, request).respond(request)
        except (anyio.get_cancelled_exc_class(), ClientDisconnect):
            raise
        except Exception as error:
            return error_response(request_error(error))

    def check_request(self, request: Request) -> None:
        """Reject requests outside this live plugin-owned connection."""
        if not self.ready or not self.owner_ready:
            raise HTTPError(503, "connection is not ready")
        if request.path_params.get("plugin_id") != self.owner_id:
            raise HTTPError(404, "message connection not found")
