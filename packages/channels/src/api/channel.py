"""Own one consumer-configured HTTP message listener."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import anyio
from fastapi import FastAPI, Request
from starlette.requests import ClientDisconnect
from starlette.responses import Response
from starlette.routing import BaseRoute

from opsmesh.adapters import Channel
from opsmesh.drivers import get_driver

from ..context import UserContext
from .adapter import APIAdapter
from .attachments import AttachmentUploads
from .attachments import mount as mount_attachments
from .config import Settings
from .entrances import MessageEntrance
from .errors import (
    APIConfigurationError,
    HTTPError,
    request_error,
)
from .events import APIMessageEvent
from .exchange import MessageExchange
from .responses import error_response, json_response
from .server import Server


class APIChannel(Channel):
    """Dispatch fixed HTTP message endpoints to framework matchers."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types | {"data", "markdown", "table"}
    stream_formats = frozenset({"text", "markdown"})

    def __init__(self, settings: Settings, entrance: MessageEntrance) -> None:
        """Create a stopped listener bound to plugin message entrances."""
        self.self_id = f"api:{entrance.installation_id}:{entrance.connection}"
        self.settings = settings
        self.uploads = AttachmentUploads(settings.attachments)
        self.entrance = entrance
        self.app = FastAPI(
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            redirect_slashes=False,
        )
        self.app.add_exception_handler(HTTPError, self._http_error)
        self.server = Server(self.app, settings)
        self._mounted: dict[tuple[str, str], BaseRoute] = {}
        self._started = False
        self._connected = False
        self._active_conversations: set[str] = set()
        self._conversations_drained = anyio.Event()
        self._conversations_drained.set()

    @property
    def started(self) -> bool:
        """Whether routes are mounted and may accept requests."""
        return self._started

    async def startup(self) -> None:
        """Mount transport endpoints and register the connection once."""
        if self._started:
            return
        if self._connected:
            raise APIConfigurationError(
                "The previous API lifecycle has not stopped"
            )
        driver = get_driver()
        try:
            self.adapter = driver.get_adapter(APIAdapter)
        except KeyError:
            self.adapter = driver.register_adapter(APIAdapter)
        self._started = True
        try:
            self._mount_internal_routes()
            mount_attachments(self)
            self.adapter.connect(self)
            self._connected = True
        except BaseException:
            self._started = False
            self._unmount_all()
            raise

    def stop_accepting(self) -> None:
        """Reject new requests without releasing transport resources."""
        self._started = False
        self.server.stop_accepting()

    async def start_accepting(self) -> None:
        """Open this channel listener after consumers are ready."""
        await self.server.startup(get_driver().task_group)

    async def drain(self) -> None:
        """Finish active requests before dependent plugins close."""
        await self.server.shutdown()
        with anyio.fail_after(self.settings.shutdown_timeout):
            await self._conversations_drained.wait()

    def _release_conversation(self, scope: str) -> None:
        self._active_conversations.discard(scope)
        if not self._active_conversations:
            self._conversations_drained.set()

    async def shutdown(self) -> None:
        """Stop accepting requests and remove this channel's routes."""
        self.stop_accepting()
        await self.drain()
        self._unmount_all()
        self.uploads.clear()
        if self._connected:
            self.adapter.disconnect(self)
            self._connected = False

    async def _http_error(self, request: Request, error: HTTPError) -> Response:
        return error_response(error)

    async def send(
        self, event: APIMessageEvent, message: Any, **kwargs: Any
    ) -> Any:
        """Send a complete message in the current event's response scope."""
        return await self.call("reply", event=event, result=message, **kwargs)

    def _mount_internal_routes(self) -> None:

        async def health(request: Request) -> Response:
            return json_response({"status": "ok"})

        async def ready(request: Request) -> Response:
            is_ready = self._started and self.entrance.active
            payload: dict[str, Any] = {
                "status": "ready" if is_ready else "not_ready",
                "plugin": self.entrance.installation_id,
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
            self._mounted[(method, path)] = self.app.router.routes[-1]

    def _unmount_all(self) -> None:
        for route in tuple(self._mounted.values()):
            self.app.router.routes.remove(route)
        self._mounted.clear()

    async def _handle(self, request: Request) -> Response:
        """Authenticate a fixed message endpoint and dispatch its matchers."""
        try:
            self.check_request(request)
            return await MessageExchange(self, self.entrance).respond(request)
        except (anyio.get_cancelled_exc_class(), ClientDisconnect):
            raise
        except Exception as error:
            return error_response(request_error(error))

    def check_request(self, request: Request) -> None:
        """Reject requests outside this live plugin-owned connection."""
        if not self._started or not self.entrance.active:
            raise HTTPError(503, "connection is not ready")
        if (
            request.path_params.get("plugin_id")
            != self.entrance.installation_id
        ):
            raise HTTPError(404, "message entrance not found")

    async def resolve_caller(
        self, request: Request, stack: AsyncExitStack
    ) -> UserContext:
        """Run the consumer's identity dependency inside its request scope."""
        caller = await self.entrance.identity(
            channel=self,
            invocation_context=request,
            stack=stack,
            dependency_cache={},
        )
        if not isinstance(caller, UserContext):
            raise TypeError("The identity resolver must return UserContext")
        return caller
