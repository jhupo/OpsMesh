"""Send DingTalk replies through webhook text or native robot templates."""

import json
import time
from typing import TYPE_CHECKING, Any

from ..sending import MessageSendError, OutgoingAttachment, prepare
from .client import DingTalkClient
from .errors import DingTalkSendError
from .events import DingTalkMessageEvent
from .media import DingTalkMedia, validate

if TYPE_CHECKING:
    from .channel import DingTalkChannel

_WEBHOOK_SAFETY_MS = 5000


class DingTalkSender:
    """Own one ordered send without adding retries around SDK operations."""

    def __init__(
        self, client: DingTalkClient, channel: "DingTalkChannel"
    ) -> None:
        """Borrow the connection resources for one send operation."""
        self.client = client
        self.channel = channel

    async def send(self, event: DingTalkMessageEvent, message: Any) -> Any:
        """Validate the complete message before its first visible send."""
        parts = await prepare(
            message, event.attachments, self.channel.settings.max_message_length
        )
        if len(parts) == 1 and isinstance(parts[0], str):
            return await self._text(event, parts[0])
        target = self._target(event)
        for part in parts:
            if isinstance(part, OutgoingAttachment):
                validate(part)
        token = await self.client.access_token(self.channel.settings)
        media = DingTalkMedia(self.client, token)
        templates = []
        for part in parts:
            templates.append(
                ("sampleText", {"content": part})
                if isinstance(part, str)
                else await media.upload(part)
            )
        responses: list[dict[str, Any]] = []
        endpoint, recipient = target
        for index, (key, params) in enumerate(templates):
            try:
                response = await self.client.post_json(
                    "https://api.dingtalk.com/v1.0/robot/" + endpoint,
                    {
                        **recipient,
                        "robotCode": self.channel.settings.client_id,
                        "msgKey": key,
                        "msgParam": json.dumps(params),
                    },
                    headers={"x-acs-dingtalk-access-token": token},
                )
                if not response.get("processQueryKey"):
                    raise DingTalkSendError("DingTalk did not confirm the send")
            except Exception as error:
                if len(parts) == 1:
                    raise
                raise MessageSendError(responses, index) from error
            responses.append(response)
        return responses[0] if len(responses) == 1 else responses

    async def _text(self, event: DingTalkMessageEvent, text: str) -> dict:
        webhook = event.session_webhook
        if webhook is None:
            raise DingTalkSendError(
                "DingTalk replies require a session webhook"
            )
        expires = event.session_webhook_expires_at
        if (
            expires is not None
            and expires - int(time.time() * 1000) <= _WEBHOOK_SAFETY_MS
        ):
            raise DingTalkSendError("The DingTalk session webhook has expired")
        return await self.client.post_json(
            webhook, {"msgtype": "text", "text": {"content": text}}
        )

    @staticmethod
    def _target(event: DingTalkMessageEvent) -> tuple[str, dict]:
        if event.conversation_type == "2":
            return "groupMessages/send", {
                "openConversationId": event.conversation_id
            }
        staff_id = event.raw.get("senderStaffId")
        if (
            event.conversation_type != "1"
            or not isinstance(staff_id, str)
            or not staff_id
        ):
            raise DingTalkSendError(
                "DingTalk media replies require a staff user ID"
            )
        return "oToMessages/batchSend", {"userIds": [staff_id]}
