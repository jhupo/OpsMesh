"""Typed QQ identities, chats and media; nicknames never identify accounts."""

import re
from typing import Any

from opsmesh_sdk.channels import (
    ChannelMedia,
    ContentPart,
    Conversation,
    ExternalActor,
    InboundMessage,
)


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
    content = []
    text = re.sub(r"<@!?[A-Za-z0-9_-]+>", "", data.get("content", "")).strip()
    if text:
        content.append(ContentPart(text=text))
    for attachment in data.get("attachments", []):
        url = str(attachment.get("url", ""))
        if url.startswith("//"):
            url = "https:" + url
        key, mime = await media.fetch(url)
        family = mime.split("/")[0]
        content.append(
            ContentPart(
                type=family if family in {"image", "audio", "video"} else "file",
                media_key=key,
                mime_type=mime,
                filename=attachment.get("filename") or "attachment",
            )
        )
    if not content:
        return None
    return InboundMessage(
        event_id=str(data["id"]),
        actor=ExternalActor(
            scope=app_id,
            id_kind=identity_kind,
            external_id=str(sender),
            name=str(author.get("username") or ""),
        ),
        conversation=Conversation(external_id=str(destination), kind=kind),
        content=content,
        reply_address={"kind": reply_kind, "target": str(destination), "msg_id": str(data["id"])},
    )
