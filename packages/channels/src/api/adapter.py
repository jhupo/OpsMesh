"""Framework adapter for the host's HTTP request and response transport."""

from typing import Any

from opsmesh.adapters import Adapter, Channel
from opsmesh.messages import Message, MessageSegment

from ..sending import normalize
from .attachments import encode_message


class APIAdapter(Adapter):
    """Expose HTTP responses through the framework channel abstraction."""

    @classmethod
    def get_name(cls) -> str:
        """Return the HTTP channel protocol identifier."""
        return "api"

    async def _call(self, channel: Channel, operation: str, **data: Any) -> Any:
        if operation != "reply":
            raise ValueError(f"Unsupported API operation: {operation}")
        result = data["result"]
        if data["event"].replied:
            raise RuntimeError("An HTTP event can only receive one response")
        if isinstance(result, (Message, MessageSegment)):
            result = await encode_message(
                channel, data["event"], normalize(result)
            )
        data["event"].reply(result)
