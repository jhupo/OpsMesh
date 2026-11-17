"""HTTP message transport for framework handlers."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .adapter import APIAdapter
    from .channel import APIChannel
    from .config import APIConnectionSettings
    from .errors import APIConfigurationError, APIError, HTTPError
    from .events import APIMessageEvent
    from .schemas import MessageInput, MessageRequest

_EXPORTS = {
    "APIAdapter": ".adapter",
    "APIChannel": ".channel",
    "APIConnectionSettings": ".config",
    "APIConfigurationError": ".errors",
    "APIError": ".errors",
    "APIMessageEvent": ".events",
    "HTTPError": ".errors",
    "MessageInput": ".schemas",
    "MessageRequest": ".schemas",
}


def __getattr__(name: str) -> object:
    """Import protocol classes only when a consumer requests them."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"Unknown API channel attribute: {name}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def get_app(connection: str = "main") -> FastAPI:
    """Return the prepared FastAPI app for caller-owned native routes.

    The app exists from the consumer's startup phase and is listening after
    all readiness callbacks finish, so middleware can be installed first.
    """
    from opsmesh.plugins import get_current_plugin

    from ..lifecycle import protocol_adapter

    owner = get_current_plugin()
    if owner is None:
        raise RuntimeError("HTTP connections belong to consuming plugins")
    try:
        channel = protocol_adapter("api").get_connection(owner.id_, connection)
    except LookupError as error:
        raise RuntimeError(
            f"API connection {connection!r} is not prepared"
        ) from error
    return channel.app


__all__ = [
    "APIAdapter",
    "APIChannel",
    "APIConnectionSettings",
    "APIConfigurationError",
    "APIError",
    "APIMessageEvent",
    "HTTPError",
    "MessageInput",
    "MessageRequest",
    "get_app",
]
