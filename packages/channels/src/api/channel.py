"""Runtime owner for authenticated, plugin-declared HTTP routes."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio
from fastapi import Depends, FastAPI, Request
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import Response, StreamingResponse
from starlette.routing import BaseRoute

from opsmesh.adapters import Channel
from opsmesh.dispatch import dispatch_event
from opsmesh.drivers import get_driver
from opsmesh.utils import flatten_exception_group

from ..requests import read_body
from ..server import Server
from .adapter import APIAdapter
from .auth import Authenticator, Principal
from .config import Settings
from .errors import (
    APIConfigurationError,
    HTTPError,
)
from .events import HTTPEvent
from .messages import APIMessage
from .responses import (
    encode_response,
    error_response,
    json_response,
    reset_stream_error_scope,
    stream_error_scope,
)
from .routes import APIRouter, RouteRegistration
from .streaming import StreamLease

logger = logging.getLogger(__name__)

_JSON_CONTENT_TYPES = frozenset({"application/json"})


class APIChannel(Channel):
    """Mount plugin routes on the shared HTTP transport and invoke them."""

    def __init__(self, settings: Settings, router: APIRouter) -> None:
        """Create a stopped service bound to a route registry."""
        self.self_id = "api"
        self.settings = settings
        self._router = router
        self.app = FastAPI(
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            redirect_slashes=False,
        )
        self.app.add_exception_handler(HTTPError, self._http_error)
        self.server = Server(self.app, settings)
        self._authenticator: Authenticator | None = None
        self._mounted: dict[tuple[str, str], BaseRoute] = {}
        self._started = False
        self._connected = False
        router.bind_service(self)

    @classmethod
    def from_config(cls) -> APIChannel | None:
        """Configure the shared API independently of business route count."""
        from ..config import protocol_settings
        from .routing import get_router

        values = protocol_settings("api")
        router = get_router()
        if values is None:
            if router.registrations:
                raise APIConfigurationError(
                    "API routes require channels.api authentication settings"
                )
            return None
        return cls(Settings.model_validate(values), router)

    @property
    def started(self) -> bool:
        """Whether routes are mounted and may accept requests."""
        return self._started

    async def startup(self) -> None:
        """Resolve credentials and mount every declared route once."""
        if self._started:
            return
        authenticator = Authenticator.from_settings(self.settings)
        driver = get_driver()
        try:
            self.adapter = driver.get_adapter(APIAdapter)
        except KeyError:
            self.adapter = driver.register_adapter(APIAdapter)
        self._authenticator = authenticator
        self._started = True
        try:
            self._mount_internal_routes()
            for registration in self._router.registrations:
                self.mount_route(registration)
            self.adapter.connect(self)
            self._connected = True
        except BaseException:
            self._started = False
            self._unmount_all()
            self._authenticator = None
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

    async def shutdown(self) -> None:
        """Stop accepting requests and remove this channel's routes."""
        self.stop_accepting()
        await self.drain()
        self._unmount_all()
        self._authenticator = None
        if self._connected:
            self.adapter.disconnect(self)
            self._connected = False

    async def _http_error(self, request: Request, error: HTTPError) -> Response:
        return error_response(error)

    def mount_route(self, registration: RouteRegistration) -> None:
        """Bind a declared framework entrance to a native FastAPI route."""
        if not self._started:
            return
        key = (registration.method, registration.path)
        if key in self._mounted:
            return

        async def authorize(request: Request) -> Principal:
            if not self._started or self._authenticator is None:
                raise HTTPError(503, "channel is not ready")
            if not registration.active:
                raise HTTPError(503, "route is not ready")
            principal = self._authenticator.authenticate(request)
            Authenticator.authorize(principal, registration.access)
            return principal

        authenticated = Depends(authorize)

        async def entry(
            request: Request, principal: Principal = authenticated
        ) -> Response:
            response = await self._handle(registration, request, principal)
            if request.method == "HEAD" and not isinstance(
                response, StreamingResponse
            ):
                response.body = b""
            return response

        self.app.add_api_route(
            registration.path,
            entry,
            methods=["GET", "HEAD"]
            if registration.method == "GET"
            else [registration.method],
            name=registration.name,
            response_model=None,
        )
        self._mounted[key] = self.app.router.routes[-1]

    def unmount_route(self, registration: RouteRegistration) -> None:
        """Remove only the native route owned by this declaration."""
        route = self._mounted.pop(
            (registration.method, registration.path), None
        )
        if route is not None:
            self.app.router.routes.remove(route)

    async def send(self, event: HTTPEvent, message: Any, **kwargs: Any) -> Any:
        """Set the response of the current HTTP event once."""
        return await self.call("reply", event=event, result=message)

    def _mount_internal_routes(self) -> None:

        async def health(request: Request) -> Response:
            return json_response({"status": "ok"})

        async def ready(request: Request) -> Response:
            blocked = _blocked_required_plugins()
            is_ready = self._started and not blocked
            payload: dict[str, Any] = {
                "status": "ready" if is_ready else "not_ready",
                "routes": len(self._router.registrations),
            }
            if blocked:
                payload["blocked_plugins"] = blocked
            return json_response(payload, status_code=200 if is_ready else 503)

        for path, name, handler in (
            ("/healthz", "api.health", health),
            ("/readyz", "api.ready", ready),
        ):
            self.app.add_api_route(path, handler, methods=["GET"], name=name)
            self._mounted[("GET", path)] = self.app.router.routes[-1]

    def _unmount_all(self) -> None:
        for route in tuple(self._mounted.values()):
            self.app.router.routes.remove(route)
        self._mounted.clear()

    async def _handle(
        self,
        registration: RouteRegistration,
        request: Request,
        principal: Principal,
    ) -> Response:
        if not self._started or self._authenticator is None:
            return error_response(HTTPError(503, "channel is not ready"))
        if not registration.active:
            return error_response(HTTPError(503, "route is not ready"))
        try:
            token = stream_error_scope(self.settings.stream_error_message)
            try:
                return await self._invoke(registration, request, principal)
            finally:
                reset_stream_error_scope(token)
        except (HTTPException, ClientDisconnect):
            raise
        except HTTPError as error:
            return error_response(error)
        except TimeoutError:
            return error_response(HTTPError(504, "request timed out"))
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:
            logger.exception('API route "%s" failed', registration.name)
            return error_response(HTTPError(500, "internal server error"))

    async def _invoke(
        self,
        registration: RouteRegistration,
        request: Request,
        principal: Principal,
    ) -> Response:
        body = await self._parse_body(registration, request)
        event = HTTPEvent(
            request=request,
            payload=body,
            message=APIMessage.from_payload(body),
            route_name=registration.name,
            user_id=principal.caller.id,
        )
        stack = AsyncExitStack()
        await stack.__aenter__()
        resources = StreamLease(stack)
        try:
            with anyio.fail_after(self.settings.request_timeout_seconds):
                outcome = await self._dispatch(
                    registration, event, principal, stack
                )
            if outcome.errors:
                error = outcome.errors[0]
                if isinstance(error, BaseExceptionGroup):
                    leaves = list(flatten_exception_group(error))
                    if len(leaves) == 1 and isinstance(leaves[0], HTTPError):
                        raise leaves[0]
                raise error
            if not event.replied:
                raise HTTPError(403, "request was not handled")
            result = event.result
            response, streamed = encode_response(registration, result)
        except BaseException as error:
            await resources.release(event.result, error)
            raise
        if streamed:
            return resources.transfer(response)
        await resources.close()
        return response

    async def _dispatch(self, registration, event, principal, stack):
        """Cancel in-flight framework work when the HTTP peer disconnects."""
        disconnected = False
        outcome = None
        async with anyio.create_task_group() as tasks:

            async def watch_disconnect():
                nonlocal disconnected
                while True:
                    message = await event.request.receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        tasks.cancel_scope.cancel()
                        return

            tasks.start_soon(watch_disconnect)
            outcome = await dispatch_event(
                self,
                event,
                invocation_context=principal.caller,
                stack=stack,
                matcher_filter=registration.accepts_matcher,
            )
            tasks.cancel_scope.cancel()
        if disconnected:
            raise ClientDisconnect()
        return outcome

    async def _parse_body(
        self, registration: RouteRegistration, request: Request
    ) -> Any:
        model = registration.body_model
        content = await read_body(request, self.settings.max_request_body)
        if content:
            content_type = (
                request.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type and not (
                content_type in _JSON_CONTENT_TYPES
                or content_type.endswith("+json")
            ):
                raise HTTPError(415, "request content type must be JSON")
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                raise HTTPError(400, "request body is not valid JSON") from None
        else:
            payload = {}
        if model is None:
            return payload
        try:
            return model.model_validate(payload).model_dump(mode="json")
        except ValidationError:
            raise HTTPError(
                422, "request body does not match the contract"
            ) from None


def _blocked_required_plugins() -> list[str]:
    from opsmesh.plugins.registry import get_loaded_plugins

    return sorted(
        plugin.id_
        for plugin in get_loaded_plugins()
        if plugin.required and not plugin.ready
    )
