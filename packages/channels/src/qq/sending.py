"""Send ordered QQ replies after validating and uploading all media."""

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..sending import MessageSendError, OutgoingAttachment, prepare
from .client import QQClient
from .errors import QQSendError
from .events import QQMessageEvent
from .media import QQMedia, media_type

if TYPE_CHECKING:
    from .channel import QQChannel


class QQSender:
    """Bind one sender to an existing connection and authenticated client."""

    def __init__(
        self, client: QQClient, channel: "QQChannel", secret: str
    ) -> None:
        """Borrow the connection resources for one send operation."""
        self.client = client
        self.channel = channel
        self.secret = secret

    async def send(self, event: QQMessageEvent, message: Any) -> Any:
        """Return vendor responses in order; never replay a partial send."""
        parts = await prepare(
            message, event.attachments, self.channel.settings.max_message_length
        )
        for part in parts:
            if isinstance(part, OutgoingAttachment):
                media_type(part)
        async with self.channel.replies(event.message_id, len(parts)):
            return await self._send_parts(event, parts)

    async def _send_parts(
        self, event: QQMessageEvent, parts: list[str | OutgoingAttachment]
    ) -> Any:
        """Upload and send within the connection's reserved reply scope."""
        token = await self.client.access_token(
            self.channel.self_id, self.channel.settings.app_id, self.secret
        )
        base = (
            self.channel.protocol.sandbox_api_base
            if self.channel.settings.sandbox
            else self.channel.protocol.api_base
        )
        recipient = (
            ("groups", event.group_id)
            if event.group_id
            else ("users", event.user_id)
        )
        target = f"{base}/v2/{recipient[0]}/{quote(recipient[1], safe='')}"
        media = QQMedia(self.client, target, token)
        uploaded: dict[tuple[str, str], str] = {}
        for part in parts:
            if (
                isinstance(part, OutgoingAttachment)
                and (part.kind, part.attachment.id) not in uploaded
            ):
                uploaded[(part.kind, part.attachment.id)] = await media.upload(
                    part
                )
        responses: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            payload = {
                "msg_id": event.message_id,
                "msg_seq": self.channel.next_sequence(event.message_id),
                "msg_type": 0 if isinstance(part, str) else 7,
            }
            if isinstance(part, str):
                payload["content"] = part
            else:
                payload["content"] = " "
                payload["media"] = {
                    "file_info": uploaded[(part.kind, part.attachment.id)]
                }
            try:
                response = await self.client.post_json(
                    target + "/messages", payload, headers=media.headers
                )
                if not response.get("id"):
                    raise QQSendError("QQ did not confirm the send")
            except Exception as error:
                if len(parts) == 1:
                    raise
                raise MessageSendError(responses, index) from error
            responses.append(response)
        return responses[0] if len(responses) == 1 else responses
