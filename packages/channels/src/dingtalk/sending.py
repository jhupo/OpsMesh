"""Send explicitly formatted DingTalk replies and native media templates."""

import json
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from yarl import URL

from ..receipts import Delivery
from ..sending import (
    MessageSendError,
    OutgoingAttachment,
    OutgoingMarkdown,
    normalize,
    prepare,
    require_supported,
)
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

    async def send(
        self,
        event: DingTalkMessageEvent,
        message: Any,
        *,
        delivery: Delivery | None = None,
        message_id: str | None = None,
    ) -> Any:
        """Validate the complete message before its first visible send."""
        delivery = delivery or Delivery()
        delivery.check_owner()
        message_id = message_id or uuid4().hex
        original = normalize(message)
        require_supported(original, self.channel.send_types)
        kinds = [
            part.type
            for part in original
            if part.type != "data" and (not part.is_text() or str(part).strip())
        ]
        parts = await prepare(
            original,
            event.attachments,
            self.channel.settings.max_message_length,
        )
        if len(parts) == 1 and isinstance(parts[0], (str, OutgoingMarkdown)):
            webhook = self._webhook_url(event)
            async with delivery.operation(
                message_id,
                original_type=kinds[0],
                actual_type="markdown"
                if isinstance(parts[0], OutgoingMarkdown)
                else "text",
            ) as operation:
                operation.response = await self._webhook(webhook, parts[0])
            return operation.response
        target = self._target(event)
        for part in parts:
            if isinstance(part, OutgoingAttachment):
                validate(part)
        token = await self.client.access_token(self.channel.settings)
        media = DingTalkMedia(self.client, token)
        templates = []
        for part in parts:
            if isinstance(part, OutgoingMarkdown):
                templates.append(
                    (
                        "sampleMarkdown",
                        {"title": part.title, "text": part.text},
                    )
                )
            elif isinstance(part, str):
                templates.append(("sampleText", {"content": part}))
            else:
                templates.append(await media.upload(part))
        responses: list[dict[str, Any]] = []
        endpoint, recipient = target
        for index, (key, params) in enumerate(templates):
            try:
                async with delivery.operation(
                    message_id,
                    position=index,
                    original_type=kinds[index],
                    actual_type="markdown"
                    if key == "sampleMarkdown"
                    else ("text" if key == "sampleText" else kinds[index]),
                ) as operation:
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
                        raise DingTalkSendError(
                            "DingTalk did not confirm the send"
                        )
                    operation.response = response
            except Exception as error:
                if len(parts) == 1:
                    raise
                raise MessageSendError(responses, index) from error
            responses.append(response)
        return responses[0] if len(responses) == 1 else responses

    @staticmethod
    def _webhook_url(event: DingTalkMessageEvent) -> str:
        webhook = event.session_webhook
        if webhook is None:
            raise DingTalkSendError(
                "DingTalk replies require a session webhook"
            )
        parsed = URL(webhook)
        if parsed.scheme != "https" or not parsed.host:
            raise DingTalkSendError("The DingTalk reply webhook is invalid")
        expires = event.session_webhook_expires_at
        if (
            expires is not None
            and expires - int(time.time() * 1000) <= _WEBHOOK_SAFETY_MS
        ):
            raise DingTalkSendError("The DingTalk session webhook has expired")
        return webhook

    async def _webhook(
        self, webhook: str, content: str | OutgoingMarkdown
    ) -> dict:
        payload = (
            {
                "msgtype": "markdown",
                "markdown": {"title": content.title, "text": content.text},
            }
            if isinstance(content, OutgoingMarkdown)
            else {"msgtype": "text", "text": {"content": content}}
        )
        return await self.client.post_json(webhook, payload)

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
