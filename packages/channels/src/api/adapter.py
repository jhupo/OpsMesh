"""HTTP response operations for one API connection."""

from __future__ import annotations

from typing import Any

from ..adapter import ChannelAdapter
from ..operations import UnsupportedOperation
from .channel import APIChannel
from .errors import APIConfigurationError, APIError
from .events import APIMessageEvent


class APIAdapter(ChannelAdapter[APIChannel]):
    """Expose one request's response through the framework channel contract."""

    channel_type = APIChannel
    configuration_error = APIConfigurationError
    send_error = APIError
    message_event_type = APIMessageEvent

    @classmethod
    def get_name(cls) -> str:
        """Return the HTTP channel protocol identifier."""
        return "api"

    async def _perform(
        self, channel: APIChannel, operation: str, **data: Any
    ) -> Any:
        event: APIMessageEvent = data["event"]
        if operation == "send_message":
            return await event.output.send(
                data["message"], delivery=data.get("delivery")
            )
        if operation == "stream":
            if "format" in data:
                raise TypeError("Stream format belongs to StreamMessageStart")
            return await event.output.stream(
                data["source"], delivery=data.get("delivery")
            )
        raise UnsupportedOperation(
            f"Unsupported API channel operation: {operation}"
        )
