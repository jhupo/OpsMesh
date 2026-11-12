"""Runtime owner for authenticated, plugin-declared HTTP routes."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio
from pydantic import ValidationError

from opsmesh.framework import runtime
from opsmesh.framework.adapters import Channel
from opsmesh.framework.dispatch import dispatch_event
from opsmesh.framework.drivers import (
    URL,
    ASGIMixin,
    HTTPServerSetup,
    Request,
    Response,
)
from opsmesh.framework.utils import flatten_exception_group

from .adapter import APIAdapter
from .auth import Authenticator, Principal
from .config import Settings
from .errors import (
    APIConfigurationError,
    HTTPError,
    RouteRegistrationError,
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
    """Mount plugin routes on the host HTTP driver and invoke them."""

    def __init__(self, settings: Settings, router: APIRouter) -> None:
        """Create a stopped service bound to a route registry."""
        self.self_id = "api"
        self.settings = settings
        self._router = router
        self._driver: ASGIMixin | None = None
        self._authenticator: Authenticator | None = None
        self._mounted: dict[tuple[str, str], HTTPServerSetup] = {}
        self._started = False
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
        driver = runtime.get_driver()
        if not isinstance(driver, ASGIMixin):
            raise APIConfigurationError(
                "The API channel requires an ASGI HTTP driver"
            )
        self.adapter = APIAdapter(driver)
        self._authenticator = authenticator
        self._driver = driver
        self._started = True
        try:
            self._mount_internal_routes()
            for registration in self._router.registrations:
                self.mount_route(registration)
        except BaseException:
            self._started = False
            self._unmount_all()
            self._authenticator = None
            self._driver = None
            raise

    def stop_accepting(self) -> None:
        """Reject new requests without releasing transport resources."""
        self._started = False

    async def shutdown(self) -> None:
        """Stop accepting requests and remove this channel's routes."""
        self.stop_accepting()
        self._unmount_all()
        self._authenticator = None
        self._driver = None

    def mount_route(self, registration: RouteRegistration) -> None:
        """Mount one declared route on the active driver."""
        driver = self._driver
        if driver is None or not self._started:
            return
        key = (registration.method, registration.path)
        if key in self._mounted:
            return

        async def entry(request: Request) -> Response:
            return await self._handle(registration, request)

        setup = HTTPServerSetup(
            URL(registration.path),
            registration.method,
            registration.name,
            entry,
        )
        try:
            driver.setup_http_server(setup)
        except ValueError as error:
            raise RouteRegistrationError(
                f'Cannot mount route "{registration.name}" for '
                f'"{registration.installation_id}": {error}'
            ) from error
        except RuntimeError as error:
            raise RouteRegistrationError(
                f'Route "{registration.name}" cannot be mounted after the '
                f"HTTP listener started"
            ) from error
        self._mounted[key] = setup

    def unmount_route(self, registration: RouteRegistration) -> None:
        """Remove a route if this service mounted it."""
        key = (registration.method, registration.path)
        if key not in self._mounted:
            return
        del self._mounted[key]
        driver = self._driver
        if driver is None:
            return
        try:
            driver.remove_http_server(*key)
        except NotImplementedError:
            logger.warning(
                "HTTP driver cannot remove route %s %s",
                registration.method,
                registration.path,
            )
        except Exception:
            logger.exception(
                "Failed to remove route %s %s",
                registration.method,
                registration.path,
            )

    async def send(self, event: HTTPEvent, message: Any, **kwargs: Any) -> Any:
        """Set the response of the current HTTP event once."""
        return await self.call("reply", event=event, result=message)

    def _mount_internal_routes(self) -> None:
        driver = self._driver
        if driver is None:
            return

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
            setup = HTTPServerSetup(URL(path), "GET", name, handler)
            try:
                driver.setup_http_server(setup)
            except Exception as error:
                raise APIConfigurationError(
                    f"Cannot mount API route {path}: {error}"
                ) from error
            self._mounted[("GET", path)] = setup

    def _unmount_all(self) -> None:
        driver = self._driver
        mounted = tuple(self._mounted)
        self._mounted.clear()
        if driver is None:
            return
        for method, path in reversed(mounted):
            try:
                driver.remove_http_server(method, path)
            except NotImplementedError:
                logger.warning(
                    "HTTP driver cannot remove route %s %s", method, path
                )
            except Exception:
                logger.exception("Failed to remove route %s %s", method, path)

    async def _handle(
        self, registration: RouteRegistration, request: Request
    ) -> Response:
        if not self._started or self._authenticator is None:
            return error_response(HTTPError(503, "channel is not ready"))
        if not registration.active:
            return error_response(HTTPError(503, "route is not ready"))
        try:
            principal = self._authenticator.authenticate(request)
            Authenticator.authorize(principal, registration.access)
            token = stream_error_scope(self.settings.stream_error_message)
            try:
                return await self._invoke(registration, request, principal)
            finally:
                reset_stream_error_scope(token)
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
        body = self._parse_body(registration, request)
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
                outcome = await dispatch_event(
                    self,
                    event,
                    invocation_context=principal.caller,
                    stack=stack,
                    matcher_filter=registration.accepts_matcher,
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

    def _parse_body(
        self, registration: RouteRegistration, request: Request
    ) -> Any:
        model = registration.body_model
        content = request.content or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
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
    from opsmesh.framework.plugins.registry import get_loaded_plugins

    return sorted(
        plugin.id_
        for plugin in get_loaded_plugins()
        if plugin.required and not plugin.ready
    )
