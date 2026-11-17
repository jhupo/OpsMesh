"""Framework adapter for the host's HTTP request and response transport."""

from typing import Any

from opsmesh.adapters import Adapter, Channel

from .events import APIMessageEvent


class APIAdapter(Adapter):
    """Expose HTTP responses through the framework channel abstraction."""

    @classmethod
    def get_name(cls) -> str:
        """Return the HTTP channel protocol identifier."""
        return "api"

    async def _call(self, channel: Channel, operation: str, **data: Any) -> Any:
        event = data["event"]
        if not isinstance(event, APIMessageEvent):
            raise TypeError("API operations require an APIMessageEvent")
        if operation == "reply":
            return await event.output.send(
                data["result"], delivery=data.get("delivery")
            )
        if operation == "stream":
            if "format" in data:
                raise TypeError("Set stream format on StreamMessageStart")
            return await event.output.stream(
                data["source"], delivery=data.get("delivery")
            )
        raise ValueError(f"Unsupported API operation: {operation}")
