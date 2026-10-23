"""Typed QQ identities, chats and media; nicknames never identify accounts."""

from typing import Any

from opsmesh_sdk.channels import (
    ChannelMedia,
    Conversation,
    ExternalActor,
    InboundMessage,
    MessageContext,
    MessageReference,
)

from opsmesh.gateway.adapters.qq.content import attached_context, content_parts


async def parse_event(
    payload: dict[str, Any], app_id: str, media: ChannelMedia
) -> InboundMessage | None:
    event = payload.get("t")
    if event not in {
        "C2C_MESSAGE_CREATE",
        "GROUP_AT_MESSAGE_CREATE",
        "GROUP_MESSAGE_CREATE",
        "AT_MESSAGE_CREATE",
        "DIRECT_MESSAGE_CREATE",
    }:
        return None
    data = payload["d"]
    author = data.get("author", {})
    if event == "C2C_MESSAGE_CREATE":
        kind, identity_kind = "private", "user_openid"
        sender = author.get("user_openid") or data.get("user_openid")
        destination, reply_kind = sender, "c2c"
    elif event in {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
        kind, identity_kind = "group", "member_openid"
        sender = author.get("member_openid") or data.get("member_openid")
        destination, reply_kind = data.get("group_openid"), "group"
    else:
        kind, identity_kind = (
            ("private" if event == "DIRECT_MESSAGE_CREATE" else "guild"),
            "guild_user_id",
        )
        sender = author.get("id")
        destination = data.get("guild_id") if kind == "private" else data.get("channel_id")
        reply_kind = "dm" if kind == "private" else "channel"
    if not sender or not destination or not data.get("id"):
        raise ValueError("QQ event lacks stable identity or message ID")
    mentions = [str(item["id"]) for item in data.get("mentions", []) if item.get("id")]
    reference = data.get("message_reference") or {}
    role = author.get("member_role")
    mentioned = event in {"GROUP_AT_MESSAGE_CREATE", "AT_MESSAGE_CREATE"}
    content = await content_parts(data, media, mentioned=mentioned)
    recent, quoted = await attached_context(data, media)
    content = (quoted + content)[:100]
    if not content:
        return None
    return InboundMessage(
        event_id=str(data["id"]),
        actor=ExternalActor(
            scope=app_id,
            id_kind=identity_kind,
            external_id=str(sender),
            name=str(author.get("username") or ""),
            external_role=role if role in {"owner", "admin", "member"} else None,
        ),
        conversation=Conversation(external_id=str(destination), kind=kind),
        content=content,
        context=MessageContext(
            event_type=str(event),
            bot_mentioned=event in {"GROUP_AT_MESSAGE_CREATE", "AT_MESSAGE_CREATE"}
            or any(item.get("is_you") is True for item in data.get("mentions", [])),
            mentions=mentions,
            reference=MessageReference(message_id=str(reference["message_id"]))
            if reference.get("message_id")
            else None,
            visibility="all"
            if event == "GROUP_MESSAGE_CREATE"
            else "mention_recent"
            if recent
            else "mention_only",
            recent_messages=recent,
        ),
        reply_address={"kind": reply_kind, "target": str(destination), "msg_id": str(data["id"])},
    )
