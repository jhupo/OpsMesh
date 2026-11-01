"""Normalize OneBot event identities and conversation routing keys."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from opsmesh.framework.adapters import BotMessageEvent, OpsMeshEvent
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.onebot.message import decode_message


class OneBotMessageEvent(BotMessageEvent):
    message_id: str
    message_type: str
    channel_id: str
    self_id: str


def decode_event(data: dict[str, Any], *, account_id: str, self_id: str) -> OpsMeshEvent:
    if str(data.get("self_id")) != self_id:
        raise OpsMeshError("INVALID_REQUEST", "Webhook bot identity does not match account")
    post_type = data.get("post_type")
    if post_type not in {"message", "message_sent", "notice", "request", "meta_event"}:
        raise OpsMeshError("INVALID_REQUEST", "Unknown OneBot post_type")
    try:
        occurred_at = datetime.fromtimestamp(float(data["time"]), UTC)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise OpsMeshError("INVALID_REQUEST", "OneBot event requires a valid time") from exc
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    event_id = f"onebot:{account_id}:{digest}"
    if post_type not in {"message", "message_sent"}:
        return OpsMeshEvent(
            id=event_id,
            type=f"onebot.{post_type}",
            source=f"onebot:{account_id}",
            occurred_at=occurred_at,
            payload=data,
        )
    kind = data.get("message_type")
    if (
        kind not in {"private", "group"}
        or data.get("user_id") is None
        or data.get("message_id") is None
    ):
        raise OpsMeshError("INVALID_REQUEST", "Incomplete OneBot message event")
    user_id = str(data["user_id"])
    channel = f"private:{user_id}" if kind == "private" else f"group:{data.get('group_id')}"
    if kind == "group" and data.get("group_id") is None:
        raise OpsMeshError("INVALID_REQUEST", "Group message requires group_id")
    # Message ID is the protocol identity; replayed payload formatting doesn't change it.
    event_id = f"onebot:{account_id}:{post_type}:{channel}:{data['message_id']}"
    message = decode_message(data.get("message"))
    to_me = kind == "private" or any(
        s.type == "onebot:at" and str(s.data.get("qq")) == self_id for s in message
    )
    return OneBotMessageEvent(
        id=event_id,
        type="message" if post_type == "message" else "onebot.message_sent",
        source=f"onebot:{account_id}",
        occurred_at=occurred_at,
        message=message,
        user_id=user_id,
        session_id=f"{account_id}:{channel}:{user_id}",
        to_me=to_me,
        message_id=str(data["message_id"]),
        message_type=kind,
        channel_id=channel,
        self_id=self_id,
    )
