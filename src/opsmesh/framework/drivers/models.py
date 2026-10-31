"""Small transport contracts independent of HTTP libraries."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opsmesh.framework.drivers.base import WebSocketServerConnection


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    timeout: float = 30.0


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


def _validate_server_path(path: str) -> None:
    """Ingress v1 uses explicit paths, never wildcard routes over the platform API."""
    if (
        not re.fullmatch(r"/[A-Za-z0-9._~/-]+", path)
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Server paths must be non-root literal absolute URL paths")
    if path.split("/")[1] in {
        "api",
        "docs",
        "openapi",
        "openapi.json",
        "health",
        "metrics",
        "redoc",
    }:
        raise ValueError(f"Server path is reserved by the application: {path}")


@dataclass(frozen=True)
class HTTPServerSetup:
    """An async protocol handler. The handler owns signature/authentication checks."""

    path: str
    methods: frozenset[str]
    handler: Callable[[HTTPRequest], Awaitable[HTTPResponse]]

    def __post_init__(self) -> None:
        _validate_server_path(self.path)
        methods = frozenset(method.upper() for method in self.methods)
        if not methods or not methods <= {
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        }:
            raise ValueError("HTTP server methods must be a non-empty set of supported methods")
        # Starlette adds HEAD to GET routes. Reserve it here too, before mounting.
        if "GET" in methods:
            methods |= {"HEAD"}
        object.__setattr__(self, "methods", methods)


@dataclass(frozen=True)
class WebSocketServerSetup:
    """Handlers inspect the upgrade request and explicitly accept after authentication."""

    path: str
    handler: Callable[[HTTPRequest, WebSocketServerConnection], Awaitable[None]]

    def __post_init__(self) -> None:
        _validate_server_path(self.path)
