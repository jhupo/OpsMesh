import hashlib
import json
from typing import Any

from dingtalk_stream import CardCallbackMessage, ChatbotMessage
from opsmesh_sdk.channels import ChannelEvent


def public_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: public_data(item)
            for key, item in value.items()
            if key.lower()
            not in {
                "sessionwebhook",
                "sessionwebhookexpiredtime",
                "access_token",
                "accesstoken",
                "authorization",
                "signature",
                "token",
            }
        }
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return value


def stable_id(frame: Any, category: str) -> str:
    identity = frame.headers.event_id or frame.headers.message_id
    if not identity:
        identity = hashlib.sha256(json.dumps(frame.data, sort_keys=True).encode()).hexdigest()
    return f"{category}:{identity}"


def chatbot(frame: Any) -> ChannelEvent:
    message = ChatbotMessage.from_dict(frame.data)
    sender = message.sender_staff_id or message.sender_id
    if not sender or not message.conversation_id or not message.message_id:
        raise ValueError("DingTalk message has no stable sender, conversation or ID")
    scope = "private" if str(message.conversation_type) == "1" else "group"
    parts = [{"type": "text", "data": {"text": text}} for text in message.get_text_list() or []]
    parts.extend(
        {"type": "image", "data": {"download_code": code}}
        for code in message.get_image_list() or []
    )
    if not parts:
        parts = [
            {"type": str(message.message_type), "data": public_data(frame.data.get("content", {}))}
        ]
    return ChannelEvent(
        id=f"message:{message.message_id}",
        type=f"message.{message.message_type}",
        external_user_id=str(sender),
        conversation_id=f"{scope}:{message.conversation_id}",
        text="\n".join(message.get_text_list() or []).strip(),
        is_message=True,
        scope=scope,
        external_role="group_admin" if message.is_admin else None,
        reply_target={
            "kind": "session",
            "webhook": message.session_webhook,
            "expires_at": message.session_webhook_expired_time,
        },
        content=parts,
        data=public_data(frame.data),
    )


def callback(frame: Any, category: str) -> ChannelEvent:
    sender = None
    if category == "card":
        message = CardCallbackMessage.from_dict(frame.data)
        sender = message.user_id or None
    return ChannelEvent(
        id=stable_id(frame, category),
        type=f"{category}.{frame.headers.event_type or frame.headers.topic or 'callback'}",
        external_user_id=sender,
        data=public_data(frame.data),
    )
