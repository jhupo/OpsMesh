"""Runtime owner for authenticated, plugin-declared HTTP routes."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack, contextmanager
from functools import partial
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
from opsmesh.plugins.invocation import PluginUnavailable
from opsmesh.utils import flatten_exception_group

from ..attachments import AttachmentCollection, AttachmentError
from ..requests import read_body
from ..server import Server
from .adapter import APIAdapter
from .attachments import AttachmentUploads
from .attachments import mount as mount_attachments
from .auth import Authenticator, Principal
from .config import Settings
from .errors import (
    APIConfigurationError,
    HTTPError,
)
from .events import APIMessageEvent, HTTPEvent
from .messages import from_request
from .output import MessageOutput
from .responses import (
    encode_response,
    error_response,
    json_response,
    reset_stream_error_scope,
    sse_event,
    stream_error_scope,
)
from .routes import APIRouter, RouteRegistration
from .streaming import MessageStreamingResponse, StreamLease

logger = logging.getLogger(__name__)

_JSON_CONTENT_TYPES = frozenset({"application/json"})


class APIChannel(Channel):
    """Mount plugin routes on the shared HTTP transport and invoke them."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types | {"data"}

    def __init__(self, settings: Settings, router: APIRouter) -> None:
        """Create a stopped service bound to a route registry."""
        self.self_id = "api"
        self.settings = settings
        self.uploads = AttachmentUploads(settings.attachments)
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
        self._active_conversations: set[str] = set()
        self._conversations_drained = anyio.Event()
        self._conversations_drained.set()
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
        if self._connected:
            raise APIConfigurationError(
                "The previous API lifecycle has not stopped"
            )
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
            mount_attachments(self)
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
        self._authenticator = None
        self.uploads.clear()
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
        """Send a complete message in the current event's response scope."""
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
                return await self._invoke_request(
                    registration, request, principal
                )
            finally:
                reset_stream_error_scope(token)
        except (HTTPException, ClientDisconnect):
            raise
        except (AttachmentError, ValidationError):
            return error_response(HTTPError(422, "Invalid message attachments"))
        except HTTPError as error:
            return error_response(error)
        except PluginUnavailable:
            return error_response(HTTPError(503, "route is not ready"))
        except TimeoutError:
            return error_response(HTTPError(504, "request timed out"))
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:
            logger.exception('API route "%s" failed', registration.name)
            return error_response(HTTPError(500, "internal server error"))

    async def _invoke_request(
        self,
        registration: RouteRegistration,
        request: Request,
        principal: Principal,
    ) -> Response:
        if registration.message_handlers:
            return await self._invoke_message(registration, request, principal)
        stack = AsyncExitStack()
        await stack.__aenter__()
        resources = StreamLease(stack)
        event = None
        try:
            from opsmesh.plugins.invocation import hold_plugin

            admitted_call = hold_plugin(registration.owner, stack)
            body = await self._parse_body(registration, request)
            files = AttachmentCollection(self.settings.attachments)
            values = dict(
                request=request,
                payload=body,
                attachments=files,
                route_name=registration.name,
                installation_id=registration.installation_id,
                user_id=principal.caller.id,
            )
            if registration.message_handlers:
                message = from_request(
                    body,
                    files,
                    partial(
                        self.uploads.resolve,
                        (registration.installation_id, principal.caller.id),
                    ),
                )
                event = APIMessageEvent(message=message, **values)
            else:
                event = HTTPEvent(**values)
            with anyio.fail_after(self.settings.request_timeout_seconds):
                outcome = await self._dispatch(
                    registration, event, principal, stack, admitted_call
                )
            if outcome.errors:
                error = outcome.errors[0]
                if isinstance(error, BaseExceptionGroup):
                    leaves = list(flatten_exception_group(error))
                    if len(leaves) == 1 and isinstance(leaves[0], HTTPError):
                        raise leaves[0]
                raise error
            if not event.replied:
                await resources.close()
                return Response(status_code=204)
            result = event.result
            response, streamed = encode_response(registration, result)
        except BaseException as error:
            await resources.release(
                event.result if event is not None else None, error
            )
            raise
        if streamed:
            from opsmesh.plugins.invocation import invocation_scope
            from opsmesh.plugins.registry import plugin_scope

            @contextmanager
            def execution_scope():
                with (
                    plugin_scope(registration.owner),
                    invocation_scope(admitted_call),
                ):
                    yield

            return resources.transfer(response, execution_scope=execution_scope)
        await resources.close()
        return response

    async def _invoke_message(
        self, registration, request, principal
    ) -> Response:
        from opsmesh.plugins.invocation import hold_plugin

        stack = AsyncExitStack()
        await stack.__aenter__()
        resources = StreamLease(stack)
        try:
            admitted_call = hold_plugin(registration.owner, stack)
            body = await self._parse_body(registration, request)
            files = AttachmentCollection(self.settings.attachments)
            message = from_request(
                body,
                files,
                partial(
                    self.uploads.resolve,
                    (registration.installation_id, principal.caller.id),
                ),
            )
            values = dict(
                request=request,
                payload=body,
                attachments=files,
                route_name=registration.name,
                installation_id=registration.installation_id,
                user_id=principal.caller.id,
                message=message,
                connection_id=self.self_id,
            )
            if body.conversation_id is not None:
                values["conversation_id"] = body.conversation_id
            event = APIMessageEvent(**values)
            scope = event.get_session_id()
            if scope in self._active_conversations:
                raise HTTPError(409, "conversation is busy")
            if not self._active_conversations:
                self._conversations_drained = anyio.Event()
            self._active_conversations.add(scope)
            stack.callback(self._release_conversation, scope)
            output = MessageOutput(self, event)
            event.bind_output(output)
        except BaseException as error:
            await resources.close(error)
            raise

        async def process(streamed):
            from opsmesh.plugins.invocation import invocation_scope
            from opsmesh.plugins.registry import plugin_scope

            failure = None
            status = "completed"
            with (
                plugin_scope(registration.owner),
                invocation_scope(admitted_call),
            ):
                try:
                    if streamed:
                        outcome = await dispatch_event(
                            self,
                            event,
                            invocation_context=principal.caller,
                            stack=stack,
                            matcher_filter=registration.accepts_matcher,
                            admitted_call=admitted_call,
                        )
                    else:
                        with anyio.fail_after(
                            self.settings.request_timeout_seconds
                        ):
                            outcome = await self._dispatch(
                                registration,
                                event,
                                principal,
                                stack,
                                admitted_call,
                            )
                    if outcome.errors:
                        raise outcome.errors[0]
                    if output.failure is not None:
                        raise output.failure
                    status = "waiting" if outcome.suspended else "completed"
                except BaseException as error:
                    failure = error
                finally:
                    output.close()
                    try:
                        await resources.close(failure)
                    except BaseException as cleanup_error:
                        failure = failure or cleanup_error
            if isinstance(
                failure, (anyio.get_cancelled_exc_class(), ClientDisconnect)
            ):
                raise failure
            if failure is not None:
                return "failed", _message_error(failure)
            return status, None

        accepts = request.headers.get("accept", "")
        if any(
            part.split(";", 1)[0].strip() == "text/event-stream"
            for part in accepts.split(",")
        ):

            async def produce(emit):
                output.emit = emit
                status, failure = await process(True)
                if failure is not None:
                    _, error = failure
                    payload = {
                        "conversation_id": event.conversation_id,
                        **error,
                    }
                    if output.current_message_id is not None:
                        payload["message_id"] = output.current_message_id
                    await emit(sse_event("error", payload))
                else:
                    await emit(
                        sse_event(
                            "done",
                            {
                                "conversation_id": event.conversation_id,
                                "status": status,
                            },
                        )
                    )

            async def close(error):
                output.close()
                await resources.close(error)

            return MessageStreamingResponse(
                produce,
                capacity=self.settings.stream_buffer_size,
                on_close=close,
            )
        status, failure = await process(False)
        if failure is not None:
            code, error = failure
            return json_response(
                output.envelope(status, error), status_code=code
            )
        return json_response(output.envelope(status))

    async def _dispatch(
        self, registration, event, principal, stack, admitted_call
    ):
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
                admitted_call=admitted_call,
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
            return model.model_validate(payload)
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


def _message_error(error: BaseException) -> tuple[int, dict[str, str]]:
    """Keep unexpected handler and cleanup details out of response bodies."""
    leaves = (
        list(flatten_exception_group(error))
        if isinstance(error, BaseExceptionGroup)
        else [error]
    )
    if len(leaves) == 1:
        error = leaves[0]
    if isinstance(error, HTTPError):
        status, message = error.status_code, error.message
    elif isinstance(error, PluginUnavailable):
        status, message = 503, "route is not ready"
    elif isinstance(error, TimeoutError):
        status, message = 504, "request timed out"
    elif isinstance(error, PermissionError):
        status, message = 403, "request is not permitted"
    else:
        logger.error(
            "API message processing failed",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )
        status, message = 500, "internal server error"
    return status, {"code": str(status), "message": message}
