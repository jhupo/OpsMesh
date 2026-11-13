"""Plugin-owned HTTP route registration over the framework transport."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, TypeAdapter

from opsmesh.dependencies import Dependent, Param
from opsmesh.dependencies.context import ContextParam
from opsmesh.dependencies.params import (
    ChannelParam,
    DefaultParam,
    DependParam,
    EventParam,
    MatcherParam,
    StateParam,
)
from opsmesh.matchers import Matcher, MatcherSource
from opsmesh.permissions import Permission
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.registry import (
    _current_plugin,
    track_registration,
)
from opsmesh.rules import Rule

from .errors import RouteConflict, RouteRegistrationError
from .events import HTTPHandler
from .messages import MessageRequest

if TYPE_CHECKING:
    from .channel import APIChannel

Access = Literal["invoke", "admin"]

ACCESS_INVOKE: Access = "invoke"
ACCESS_ADMIN: Access = "admin"

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_INSTALLATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_PARAM_SEGMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_ALLOW_TYPES: tuple[type[Param], ...] = (
    DependParam,
    ContextParam,
    ChannelParam,
    EventParam,
    MatcherParam,
    StateParam,
    DefaultParam,
)


def normalize_relative_path(path: str) -> str:
    """Validate and normalize a plugin-declared relative path."""
    if not path.startswith("/"):
        raise RouteRegistrationError("Route paths must start with '/'")
    if "?" in path or "#" in path:
        raise RouteRegistrationError(
            "Route paths cannot contain a query or fragment"
        )
    segments: list[str] = []
    for segment in path.split("/")[1:]:
        if not segment or segment == ".":
            continue
        if segment == "..":
            raise RouteRegistrationError("Route paths cannot traverse upward")
        if "\\" in segment or any(ord(char) < 0x20 for char in segment):
            raise RouteRegistrationError(
                "Route paths contain an invalid character"
            )
        if "{" in segment or "}" in segment:
            if _PARAM_SEGMENT.match(segment) is None:
                raise RouteRegistrationError(
                    "Path parameters must occupy a whole path segment"
                )
        segments.append(segment)
    if not segments:
        raise RouteRegistrationError(
            "Plugin route paths require at least one segment"
        )
    return "/" + "/".join(segments)


def _shape(path: str) -> str:
    return "/".join(
        "{}" if _PARAM_SEGMENT.match(segment) else segment
        for segment in path.split("/")
    )


@dataclass(eq=False)
class RouteRegistration:
    """One route owned by a plugin installation."""

    installation_id: str
    method: str
    relative_path: str
    path: str
    claim: tuple[str, str]
    name: str
    access: Access
    handler: Callable[..., Any] | None
    dependent: Dependent[Any] | None
    body_model: type[BaseModel] | None
    owner: Plugin
    response_adapter: TypeAdapter[Any] | None = None
    allow_response: bool = False
    response_types: tuple[type, ...] = ()
    matcher: type[Matcher] | None = None
    message_handlers: tuple[type[Matcher], ...] = ()

    @property
    def active(self) -> bool:
        """Accept requests only while the declaring plugin is ready."""
        return self.owner.ready

    def accepts_matcher(self, matcher: type[Matcher]) -> bool:
        """Restrict one dispatch to the route and its declared commands."""
        return matcher is self.matcher or matcher in self.message_handlers


class APIRouter:
    """Validate, own, and track plugin HTTP route declarations."""

    def __init__(self) -> None:
        """Create an empty route registry."""
        self._claims: dict[tuple[str, str], RouteRegistration] = {}
        self._order: list[RouteRegistration] = []
        self._service: APIChannel | None = None

    def bind_service(self, service: APIChannel) -> None:
        """Attach the runtime that mounts routes once it starts."""
        self._service = service

    @property
    def registrations(self) -> tuple[RouteRegistration, ...]:
        """A snapshot of routes in registration order."""
        return tuple(self._order)

    def register(
        self,
        *,
        method: str,
        path: str,
        handler: Callable[..., Any] | None = None,
        body: type[BaseModel] | None = None,
        response_adapter: TypeAdapter[Any] | None = None,
        allow_response: bool = False,
        response_types: tuple[type, ...] = (),
        access: Access = ACCESS_INVOKE,
        name: str | None = None,
        rule: Rule | None = None,
        permission: Permission | None = None,
        message_handlers: tuple[type[Matcher], ...] = (),
    ) -> RouteRegistration:
        """Register one plugin route below its installation prefix.

        Declaration ownership is derived from the framework plugin scope.
        """
        method = method.upper()
        if method not in _METHODS:
            raise RouteRegistrationError(f"Unsupported route method: {method}")
        if access not in (ACCESS_INVOKE, ACCESS_ADMIN):
            raise RouteRegistrationError(f"Unsupported access: {access}")
        if not message_handlers and not callable(handler):
            raise RouteRegistrationError("Route handler must be callable")
        if body is not None and not (
            isinstance(body, type) and issubclass(body, BaseModel)
        ):
            raise RouteRegistrationError("Route body must be a pydantic model")

        plugin = _current_plugin.get()
        if plugin is None:
            raise RouteRegistrationError(
                "A route must be declared while loading a plugin"
            )
        resolved_id = plugin.id_
        if _INSTALLATION_ID.match(resolved_id) is None:
            raise RouteRegistrationError(
                f"Invalid plugin installation id: {resolved_id}"
            )

        if message_handlers and (rule is not None or permission is not None):
            raise RouteRegistrationError(
                "Command entrances must declare rules and permissions on "
                "their matchers, not on the fallback route"
            )
        if message_handlers:
            if handler is not None or method != "POST":
                raise RouteRegistrationError(
                    "Message entrances bind POST matchers only"
                )
            if body is None or not issubclass(body, MessageRequest):
                raise RouteRegistrationError(
                    "Message bodies must extend MessageRequest"
                )
            for ancestor in body.__mro__:
                if ancestor is MessageRequest:
                    break
                if "message" in ancestor.__dict__.get("__annotations__", {}):
                    raise RouteRegistrationError(
                        "Message bodies cannot override message"
                    )
        for matcher in message_handlers:
            if not isinstance(matcher, type) or not issubclass(
                matcher, Matcher
            ):
                raise RouteRegistrationError("Expected a framework Matcher")
            if matcher.plugin_id != plugin.id_:
                raise RouteRegistrationError(
                    "Command matchers must belong to the route plugin"
                )
            if not matcher.block or matcher.temp:
                raise RouteRegistrationError(
                    "Command matchers must be blocking and persistent"
                )
        priorities = [item.priority for item in message_handlers]
        if len(set(priorities)) != len(priorities):
            raise RouteRegistrationError(
                "Message handlers require distinct priorities"
            )

        relative = normalize_relative_path(path)
        full_path = f"/plugins/{resolved_id}{relative}"
        claim = (method, _shape(full_path))
        existing = self._claims.get(claim)
        if existing is not None:
            if existing.path == full_path:
                raise RouteConflict(
                    f"Route {method} {full_path} is already registered "
                    f'by "{existing.installation_id}"'
                )
            raise RouteConflict(
                f"Route {method} {full_path} is ambiguous with "
                f"{existing.method} {existing.path} from "
                f'"{existing.installation_id}"'
            )

        try:
            dependent = (
                HTTPHandler.parse(call=handler, allow_types=_ALLOW_TYPES)
                if handler is not None
                else None
            )
        except ValueError as error:
            raise RouteRegistrationError(
                f"Invalid route handler for {method} {full_path}: {error}"
            ) from error

        registration = RouteRegistration(
            installation_id=resolved_id,
            method=method,
            relative_path=relative,
            path=full_path,
            claim=claim,
            name=name or f"{resolved_id}.{method.lower()}.{relative}",
            access=access,
            handler=handler,
            dependent=dependent,
            body_model=body,
            response_adapter=response_adapter,
            allow_response=allow_response,
            response_types=response_types,
            owner=plugin,
            message_handlers=tuple(message_handlers),
        )

        async def matches_route(event) -> bool:
            return event.get_event_name() == registration.name

        if dependent is not None:
            registration.matcher = Matcher.new(
                type_="",
                priority=1,
                rule=Rule(matches_route) & rule,
                permission=permission,
                handlers=[dependent],
                source=MatcherSource(
                    plugin_id=plugin.id_,
                    module_name=handler.__module__,
                ),
            )
        self._claims[claim] = registration
        self._order.append(registration)
        release = track_registration(
            lambda: self._remove(registration), handler
        )
        service = self._service
        if service is not None and service.started:
            try:
                service.mount_route(registration)
            except BaseException:
                release()
                self._remove(registration, unmount=False)
                raise
        return registration

    def _remove(
        self, registration: RouteRegistration, *, unmount: bool = True
    ) -> None:
        if self._claims.get(registration.claim) is not registration:
            return
        del self._claims[registration.claim]
        if registration.matcher is not None:
            with suppress(ValueError):
                registration.matcher.destroy()
        try:
            self._order.remove(registration)
        except ValueError:
            pass
        if unmount and self._service is not None:
            self._service.unmount_route(registration)
