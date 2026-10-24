import hashlib
import json
from typing import Any

from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    Event,
    GroupMessageCreateEvent,
    GuildMessageEvent,
    MessageEvent,
)
from opsmesh_sdk.channels import ChannelEvent
from pydantic_core import to_jsonable_python


def normalize(event: Event) -> ChannelEvent:
    data: dict[str, Any] = event.model_dump(mode="json", exclude={"session_id"})
    event_type = str(event.get_event_name())
    # EventType is a str Enum; use its wire value, not Enum's display name.
    event_type = getattr(event.get_event_name(), "value", event_type)
    native_id = getattr(event, "id", None) if isinstance(event, MessageEvent) else None
    identity = event.event_id or native_id
    if not identity:
        identity = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    user_id: str | None = None
    conversation: str | None = None
    target: dict[str, Any] | None = None
    scope = "private"
    try:
        user_id = event.get_user_id()
    except ValueError:
        pass
    if isinstance(event, DirectMessageCreateEvent):
        conversation = f"dms:{event.guild_id}:{user_id}"
        target = {"kind": "dms", "id": event.guild_id}
    elif isinstance(event, GuildMessageEvent):
        conversation, scope = f"guild:{event.channel_id}", "group"
        target = {"kind": "guild", "id": event.channel_id}
    elif isinstance(event, GroupMessageCreateEvent):
        conversation, scope = f"group:{event.group_openid}", "group"
        target = {"kind": "group", "id": event.group_openid}
    elif isinstance(event, C2CMessageCreateEvent):
        conversation = f"c2c:{user_id}"
        target = {"kind": "c2c", "id": user_id}
    content: list[dict[str, Any]] = []
    text = ""
    if isinstance(event, MessageEvent):
        message = event.get_message()
        content = [{"type": part.type, "data": to_jsonable_python(part.data)} for part in message]
        text = message.extract_plain_text().strip()
    return ChannelEvent(
        id=f"{event_type}:{identity}",
        type=event_type,
        external_user_id=user_id,
        conversation_id=conversation,
        text=text,
        is_message=isinstance(event, MessageEvent),
        scope=scope,
        external_role=event.author.member_role
        if isinstance(event, GroupMessageCreateEvent)
        else None,
        reply_target={**target, "message_id": native_id} if target else None,
        content=content,
        data=data,
    )
