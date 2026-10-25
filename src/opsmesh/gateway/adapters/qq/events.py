"""Normalize official botpy events into the host gateway event contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic_core import to_jsonable_python

from opsmesh.gateway.adapters.schemas import Event as GatewayEvent
from opsmesh.gateway.adapters.schemas import EventType

EVENT_TYPES: dict[str, EventType] = {
    "ready": EventType.CONNECTION_READY,
    "resumed": EventType.CONNECTION_RESUMED,
    "message_delete": EventType.MESSAGE_DELETED,
    "direct_message_delete": EventType.MESSAGE_DELETED,
    "public_message_delete": EventType.MESSAGE_DELETED,
    "message_audit_pass": EventType.MESSAGE_AUDIT_PASSED,
    "message_audit_reject": EventType.MESSAGE_AUDIT_REJECTED,
    "message_reaction_add": EventType.REACTION_ADDED,
    "message_reaction_remove": EventType.REACTION_REMOVED,
    "guild_member_add": EventType.MEMBER_JOINED,
    "guild_member_update": EventType.MEMBER_UPDATED,
    "guild_member_remove": EventType.MEMBER_LEFT,
    "interaction_create": EventType.INTERACTION_CREATED,
}


def _value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    slots = getattr(type(value), "__slots__", ())
    if slots:
        return {
            name: _value(getattr(value, name, None)) for name in slots if not name.startswith("_")
        }
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            str(name): _value(item)
            for name, item in attributes.items()
            if not str(name).startswith("_")
        }
    return to_jsonable_python(value)


def _data(event: Any) -> dict[str, Any]:
    value = _value(event)
    return value if isinstance(value, dict) else {"value": value}


def _user_id(event: Any) -> str | None:
    for candidate in (
        getattr(event, "author", None),
        getattr(event, "user", None),
        event,
    ):
        for name in ("id", "user_id", "user_openid", "member_openid", "group_member_openid"):
            value = getattr(candidate, name, None)
            if value:
                return str(value)
    return None


def normalize(event_name: str, event: Any) -> GatewayEvent:
    """Normalize a botpy object while retaining its JSON-safe native fields."""
    data = _data(event)
    native_id = getattr(event, "id", None) or getattr(event, "event_id", None)
    identity = (
        str(native_id)
        if native_id
        else hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    user_id = _user_id(event)
    event_name = event_name.lower()
    is_message = event_name in {
        "message_create",
        "at_message_create",
        "direct_message_create",
        "group_at_message_create",
        "c2c_message_create",
    }
    content = str(getattr(event, "content", "") or "") if is_message else ""
    guild_id = getattr(event, "guild_id", None)
    channel_id = getattr(event, "channel_id", None)
    group_id = getattr(event, "group_openid", None)
    conversation_id: str | None
    target: dict[str, Any] | None
    scope: str
    if event_name == "direct_message_create":
        conversation_id = f"dms:{guild_id}:{user_id}" if guild_id and user_id else None
        target = {"kind": "dms", "id": str(guild_id)} if guild_id else None
        scope = "private"
    elif event_name == "group_at_message_create":
        conversation_id = f"group:{group_id}" if group_id else None
        target = {"kind": "group", "id": str(group_id)} if group_id else None
        scope = "group"
    elif event_name == "c2c_message_create":
        conversation_id = f"c2c:{user_id}" if user_id else None
        target = {"kind": "c2c", "id": user_id} if user_id else None
        scope = "private"
    else:
        conversation_id = f"guild:{channel_id}" if channel_id else None
        target = {"kind": "guild", "id": str(channel_id)} if channel_id else None
        scope = "group"
    member = getattr(event, "member", None)
    roles = getattr(member, "roles", None)
    role = str(roles[0]) if isinstance(roles, list) and roles else None
    return GatewayEvent(
        id=f"{event_name}:{identity}",
        type=EventType.MESSAGE_CREATED
        if is_message
        else EVENT_TYPES.get(event_name, EventType.PLATFORM_EVENT),
        platform="qq",
        native_type=event_name,
        external_user_id=user_id,
        conversation_id=conversation_id,
        text=content.strip(),
        is_message=is_message,
        scope=scope,
        external_role=role,
        reply_target={**target, "message_id": str(native_id)} if target and native_id else target,
        content=[{"type": "text", "data": {"text": content}}] if content else [],
        data=data,
    )
