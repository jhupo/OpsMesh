"""Framework adapter for the host's HTTP request and response transport."""

from typing import Any

from opsmesh.adapters import Adapter, Channel
from opsmesh.messages import Message, MessageSegment

from ..sending import message_size, normalize
from .attachments import prepare_message
from .errors import HTTPError
from .events import APIMessageEvent


class APIAdapter(Adapter):
    """Expose HTTP responses through the framework channel abstraction."""

    @classmethod
    def get_name(cls) -> str:
        """Return the HTTP channel protocol identifier."""
        return "api"

    async def _call(self, channel: Channel, operation: str, **data: Any) -> Any:
        event = data["event"]
        if isinstance(event, APIMessageEvent):
            if operation == "reply":
                return await event.output.send(
                    data["result"], delivery=data.get("delivery")
                )
            if operation == "stream":
                if "format" in data:
                    raise TypeError(
                        "Stream format belongs to StreamMessageStart"
                    )
                return await event.output.stream(
                    data["source"], delivery=data.get("delivery")
                )
        if operation != "reply":
            raise ValueError(f"Unsupported API operation: {operation}")
        result = data["result"]
        if data["event"].replied:
            raise RuntimeError("An HTTP event can only receive one response")
        if isinstance(result, (Message, MessageSegment)):
            message = normalize(result)
            if message_size(message) > channel.settings.max_output_bytes:
                raise HTTPError(500, "message output limit exceeded")
            result, uploads = await prepare_message(channel, event, message)
            if uploads:
                channel.uploads.put_many(
                    (event.installation_id, event.user_id), uploads
                )
        data["event"].reply(result)
