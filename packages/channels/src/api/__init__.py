"""HTTP message transport for framework handlers."""

from fastapi import FastAPI

from opsmesh.drivers import get_driver

from .channel import APIChannel
from .config import Settings
from .entrances import (
    MessageEntrance,
    MessageEntrances,
    get_entrances,
    message,
)
from .errors import APIConfigurationError, APIError, EntranceError, HTTPError
from .events import APIMessageEvent
from .messages import MessageInput, MessageRequest


def get_app(connection: str = "main") -> FastAPI:
    """Return the running FastAPI app for native, caller-owned HTTP routes."""
    from opsmesh.plugins import get_current_plugin

    owner = get_current_plugin()
    if owner is None:
        raise RuntimeError("HTTP connections belong to consuming plugins")
    channel = get_driver().get_channel(f"api:{owner.id_}:{connection}")
    if not isinstance(channel, APIChannel) or not channel.started:
        raise RuntimeError("The API channel is not running")
    return channel.app


__all__ = [
    "APIChannel",
    "APIConfigurationError",
    "APIError",
    "APIMessageEvent",
    "EntranceError",
    "HTTPError",
    "MessageEntrance",
    "MessageEntrances",
    "MessageInput",
    "MessageRequest",
    "Settings",
    "get_app",
    "get_entrances",
    "message",
]
