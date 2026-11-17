"""Send ordered QQ replies after validating and uploading all media."""

from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import uuid4

from qqbot_agent_sdk import MediaInfo, MessageToCreate

from ..receipts import Delivery
from ..sending import (
    MessageSendError,
    OutgoingAttachment,
    prepare,
)
from .client import QQClient
from .errors import QQSendError
from .events import QQMessageEvent
from .media import media_type, upload

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

    async def send(
        self,
        event: QQMessageEvent,
        message: Any,
        *,
        delivery: Delivery | None = None,
        message_id: str | None = None,
    ) -> Any:
        """Return vendor responses in order; never replay a partial send."""
        delivery = delivery or Delivery()
        delivery.check_owner()
        message_id = message_id or uuid4().hex
        prepared = await prepare(
            message,
            event.attachments,
            self.channel.settings.max_message_length,
            supported=self.channel.send_types,
        )
        parts, kinds = prepared.parts, prepared.kinds
        for part in parts:
            if isinstance(part, OutgoingAttachment):
                media_type(part)
        async with self.channel.replies(
            event.message_id,
            len(parts),
            limit=5 if event.group_id else 4,
        ):
            return await self._send_parts(
                event, parts, delivery, message_id, kinds
            )

    async def _send_parts(
        self,
        event: QQMessageEvent,
        parts: list[str | OutgoingAttachment],
        delivery: Delivery,
        message_id: str,
        kinds: list[str],
    ) -> Any:
        """Upload and send within the connection's reserved reply scope."""
        api = self.client.account(self.channel, self.secret)
        recipient = (
            ("group", event.group_id)
            if event.group_id
            else ("c2c", event.user_id)
        )
        target = quote(recipient[1], safe="")
        upload_file = (
            api.upload_group_file if event.group_id else api.upload_c2c_file
        )
        uploaded: dict[tuple[str, str], str] = {}
        for part in parts:
            if (
                isinstance(part, OutgoingAttachment)
                and (part.kind, part.attachment.id) not in uploaded
            ):
                uploaded[(part.kind, part.attachment.id)] = await upload(
                    upload_file, target, part
                )
        responses: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            payload = MessageToCreate(
                msg_id=event.message_id,
                msg_seq=self.channel.next_sequence(event.message_id),
                msg_type=0 if isinstance(part, str) else 7,
                content=part if isinstance(part, str) else " ",
                media=(
                    None
                    if isinstance(part, str)
                    else MediaInfo(
                        file_info=uploaded[(part.kind, part.attachment.id)]
                    )
                ),
            )
            try:
                send = (
                    api.post_group_message
                    if event.group_id
                    else api.post_c2c_message
                )
                async with delivery.operation(
                    message_id,
                    position=index,
                    original_type=kinds[index],
                    actual_type="text" if isinstance(part, str) else part.kind,
                ) as operation:
                    response = await send(target, payload)
                    if not response.get("id"):
                        raise QQSendError("QQ did not confirm the send")
                    operation.response = response
            except Exception as error:
                if len(parts) == 1:
                    raise
                raise MessageSendError(responses, index) from error
            responses.append(response)
        return responses[0] if len(responses) == 1 else responses
