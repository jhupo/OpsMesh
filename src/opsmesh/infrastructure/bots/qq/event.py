"""QQ callback events normalized without persisting provider authorization tokens."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from opsmesh.framework.adapters import BotInboundEvent, BotMessageEvent, OpsMeshEvent
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.bots.qq.message import decode_message

MESSAGE_TYPES = {
    "C2C_MESSAGE_CREATE",
    "GROUP_AT_MESSAGE_CREATE",
    "GROUP_MESSAGE_CREATE",
    "AT_MESSAGE_CREATE",
    "MESSAGE_CREATE",
    "DIRECT_MESSAGE_CREATE",
}


class QQMessageEvent(BotMessageEvent):
    account_id: str
    qq_event_type: str


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise OpsMeshError("INVALID_REQUEST", f"QQ event {field} is missing or invalid")
    return value


def _identity_digest(parts: Any) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _message_index(data: JsonData) -> str:
    scene = data.get("message_scene")
    if isinstance(scene, dict) and isinstance(scene.get("ext"), list):
        for extension in scene["ext"]:
            if isinstance(extension, str) and extension.startswith("msg_idx="):
                return extension
    return str(data.get("msg_seq", data.get("seq", "")))


def decode_event(payload: JsonData, *, account_id: str, self_id: str) -> BotInboundEvent:
    kind = _identifier(payload.get("t"), "type")
    data = payload.get("d")
    if not isinstance(data, dict):
        raise OpsMeshError("INVALID_REQUEST", "QQ dispatch event data must be an object")
    if kind not in MESSAGE_TYPES:
        # The public extension payload intentionally excludes unrestricted provider data;
        # some QQ event fields contain authorization tokens or transient signed URLs.
        identifier = payload.get("id") or _identity_digest(payload)
        event = OpsMeshEvent(
            id=f"qq:{account_id}:{kind}:{identifier}",
            type=f"qq.{kind.lower()}",
            source=f"qq:{account_id}",
            payload={"event_type": kind},
        )
        return BotInboundEvent(event=event, payload={"event_type": kind, "id": identifier})
    author = data.get("author")
    if not isinstance(author, dict):
        raise OpsMeshError("INVALID_REQUEST", "QQ message author is missing")
    message_id = _identifier(data.get("id"), "message ID")
    if kind == "C2C_MESSAGE_CREATE":
        user_id = _identifier(author.get("user_openid") or author.get("id"), "user OpenID")
        channel = f"c2c:{user_id}"
    elif kind in {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
        user_id = _identifier(author.get("member_openid") or author.get("id"), "member OpenID")
        channel = f"group:{_identifier(data.get('group_openid'), 'group OpenID')}"
    else:
        user_id = _identifier(author.get("id"), "user ID")
        channel = (
            f"dms:{_identifier(data.get('guild_id'), 'direct-message guild ID')}"
            if kind == "DIRECT_MESSAGE_CREATE"
            else f"channel:{_identifier(data.get('channel_id'), 'channel ID')}"
        )
    timestamp = data.get("timestamp")
    if timestamp is not None:
        try:
            occurred_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if occurred_at.tzinfo is None:
                raise ValueError("Timestamp must have a timezone")
        except ValueError as error:
            raise OpsMeshError("INVALID_REQUEST", "QQ message timestamp is invalid") from error
    else:
        occurred_at = datetime.now(UTC)
    message = decode_message(data, self_id=self_id)
    index = _message_index(data)
    identity = _identity_digest((kind, channel, message_id, index))
    event = QQMessageEvent(
        id=f"qq:{account_id}:{identity}",
        source=f"qq:{account_id}",
        account_id=account_id,
        qq_event_type=kind,
        message_id=message_id,
        channel_id=channel,
        message=message,
        user_id=user_id,
        session_id=f"qq:{account_id}:{channel}",
        occurred_at=occurred_at,
        to_me=kind
        in {
            "C2C_MESSAGE_CREATE",
            "DIRECT_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "AT_MESSAGE_CREATE",
        },
        payload={"event_type": kind, "message_index": index},
    )
    return BotInboundEvent(
        event=event,
        payload={
            "event_type": kind,
            "channel": channel,
            "message_id": message_id,
            "index": index,
            "user_id": user_id,
            "message": message.model_dump(mode="json"),
        },
        ignored=author.get("bot") is True,
    )
