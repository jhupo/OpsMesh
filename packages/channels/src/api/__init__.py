"""Built-in generic HTTP channel for plugin-declared routes."""

from __future__ import annotations

from .channel import APIChannel
from .config import IdentitySettings, Settings
from .errors import (
    APIConfigurationError,
    APIError,
    HTTPError,
    RouteConflict,
    RouteRegistrationError,
)
from .events import HTTPEvent
from .responses import error_response, json_response, sse_event, sse_response
from .routes import (
    ACCESS_ADMIN,
    ACCESS_INVOKE,
    APIRouter,
    RouteRegistration,
)
from .routing import (
    delete,
    get,
    get_router,
    patch,
    post,
    put,
    route,
)

__all__ = [
    "ACCESS_ADMIN",
    "ACCESS_INVOKE",
    "APIError",
    "APIConfigurationError",
    "APIRouter",
    "APIChannel",
    "HTTPEvent",
    "HTTPError",
    "IdentitySettings",
    "RouteConflict",
    "RouteRegistration",
    "RouteRegistrationError",
    "Settings",
    "delete",
    "error_response",
    "get",
    "get_router",
    "json_response",
    "patch",
    "post",
    "put",
    "route",
    "sse_event",
    "sse_response",
]
