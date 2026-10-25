import hashlib
import json
from typing import Any

from dingtalk_stream import CardCallbackMessage, ChatbotMessage

from opsmesh.gateway.adapters.schemas import Event, EventType

_SENSITIVE_KEYS = {
    "sessionwebhook",
    "sessionwebhookexpiredtime",
    "access_token",
    "accesstoken",
    "authorization",
    "signature",
    "token",
}
_HEADER_FIELDS = (
    "app_id",
    "connection_id",
    "content_type",
    "message_id",
    "time",
    "topic",
    "event_born_time",
    "event_corp_id",
    "event_id",
    "event_type",
    "event_unified_app_id",
    "extensions",
)


def public_data(value: Any) -> Any:
    """Remove transport secrets while retaining the native payload shape."""
    if isinstance(value, dict):
        return {
            key: public_data(item)
            for key, item in value.items()
            if key.lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return value


def _frame_data(frame: Any, parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = getattr(frame, "headers", None)
    header_data = (
        {
            name: public_data(value)
            for name in _HEADER_FIELDS
            if (value := getattr(headers, name, None)) is not None
        }
        if headers is not None
        else {}
    )
    data: dict[str, Any] = {
        "frame_type": public_data(getattr(frame, "type", None)),
        "spec_version": public_data(getattr(frame, "spec_version", None)),
        "headers": header_data,
        "payload": public_data(frame.data),
    }
    if parsed is not None:
        data["parsed"] = public_data(parsed)
    return data


def stable_id(frame: Any, category: str) -> str:
    identity = frame.headers.event_id or frame.headers.message_id
    if not identity:
        identity = hashlib.sha256(
            json.dumps(public_data(frame.data), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    return f"{category}:{identity}"


def _content_data(value: Any) -> dict[str, Any]:
    """Expose canonical names alongside every non-secret native content field."""
    native = public_data(value)
    if not isinstance(native, dict):
        return {"value": native}
    data = dict(native)
    if "downloadCode" in native:
        data["download_code"] = native["downloadCode"]
    if "fileName" in native:
        data["name"] = native["fileName"]
    return data


def _platform_part(message_type: str, data: Any) -> dict[str, Any]:
    return {
        "type": "platform",
        "data": {
            "platform": "dingtalk",
            "type": message_type,
            "data": public_data(data),
        },
    }


def _rich_text_parts(message: ChatbotMessage) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for item in message.rich_text_content.rich_text_list if message.rich_text_content else []:
        if not isinstance(item, dict):
            parts.append(_platform_part("richText", item))
            continue
        recognized = False
        if "text" in item:
            parts.append({"type": "text", "data": {"text": item["text"]}})
            recognized = True
        if "downloadCode" in item or "pictureUrl" in item or "photoURL" in item:
            parts.append({"type": "image", "data": _content_data(item)})
            recognized = True
        if "atUser" in item or "atDingtalk" in item or "staffId" in item or "dingtalkId" in item:
            identifier = item.get("staffId") or item.get("dingtalkId") or item.get("atUser")
            parts.append(
                {
                    "type": "mention",
                    "data": {"kind": "user", "id": identifier, "native": public_data(item)},
                }
            )
            recognized = True
        if "href" in item or "url" in item:
            parts.append({"type": "link", "data": _content_data(item)})
            recognized = True
        if not recognized:
            parts.append(_platform_part("richText", item))
    return parts


def _at_user_parts(message: ChatbotMessage) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for user in message.at_users or []:
        staff_id = getattr(user, "staff_id", None)
        dingtalk_id = getattr(user, "dingtalk_id", None)
        identifier = staff_id or dingtalk_id
        if identifier is None:
            continue
        parts.append(
            {
                "type": "mention",
                "data": {
                    "kind": "user",
                    "id": identifier,
                    "native": public_data({"staff_id": staff_id, "dingtalk_id": dingtalk_id}),
                },
            }
        )
    return parts


def chatbot(frame: Any) -> Event:
    message = ChatbotMessage.from_dict(frame.data)
    sender = message.sender_staff_id or message.sender_id
    if not sender or not message.conversation_id or not message.message_id:
        raise ValueError("DingTalk message has no stable sender, conversation or ID")
    message_type = str(message.message_type or "unknown")
    scope = "private" if str(message.conversation_type) == "1" else "group"
    text_values = message.get_text_list() or []
    parts: list[dict[str, Any]] = []
    if message_type == "richText":
        parts = _rich_text_parts(message)
    elif message_type == "text":
        parts = [
            {
                "type": "text",
                "data": {
                    "text": text,
                    "native": public_data(frame.data.get("text", {})),
                },
            }
            for text in text_values
        ]
    elif message_type == "picture":
        parts = [{"type": "image", "data": _content_data(frame.data.get("content", {}))}]
    elif message_type in {"file", "audio", "video"}:
        parts = [{"type": message_type, "data": _content_data(frame.data.get("content", {}))}]
    parts.extend(_at_user_parts(message))
    if not parts:
        parts = [_platform_part(message_type, frame.data.get("content", {}))]
    parsed = {
        "message_type": message_type,
        "sender_id": str(sender),
        "conversation_id": str(message.conversation_id),
        "conversation_type": str(message.conversation_type),
        "is_in_at_list": message.is_in_at_list,
        "conversation_title": message.conversation_title,
    }
    return Event(
        id=f"message:{message.message_id}",
        type=EventType.MESSAGE_CREATED,
        platform="dingtalk",
        native_type=f"message.{message_type}",
        external_user_id=str(sender),
        conversation_id=f"{scope}:{message.conversation_id}",
        text="\n".join(str(value) for value in text_values).strip(),
        is_message=True,
        scope=scope,
        # isAdmin is retained in the sanitized native payload; it is not a platform role.
        reply_target={
            "kind": "session",
            "webhook": message.session_webhook,
            "expires_at": message.session_webhook_expired_time,
        },
        content=parts,
        data=_frame_data(frame, parsed),
    )


def callback(frame: Any, category: str) -> Event:
    sender = None
    parsed: dict[str, Any] | None = None
    content: list[dict[str, Any]] = []
    if category == "card":
        message = CardCallbackMessage.from_dict(frame.data)
        sender = message.user_id or None
        parsed = {
            "type": message.type,
            "user_id": message.user_id,
            "corp_id": message.corp_id,
            "space_id": message.space_id,
            "user_id_type": message.user_id_type,
            "card_instance_id": message.card_instance_id,
            "extension": message.extension,
            "content": message.content,
        }
        content = [_platform_part("card_callback", parsed)]
    else:
        content = [_platform_part(f"{category}_callback", frame.data)]
    event_type = frame.headers.event_type or frame.headers.topic or "callback"
    return Event(
        id=stable_id(frame, category),
        type=EventType.INTERACTION_CREATED if category == "card" else EventType.PLATFORM_EVENT,
        platform="dingtalk",
        native_type=f"{category}.{event_type}",
        external_user_id=sender,
        content=content,
        data=_frame_data(frame, parsed),
    )
