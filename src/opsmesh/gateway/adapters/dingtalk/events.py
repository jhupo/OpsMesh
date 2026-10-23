"""Preserve enterprise scope, sender ID kind, conversation and observed admin hint."""

from typing import Any

from opsmesh_sdk.channels import (
    ChannelMedia,
    ContentPart,
    Conversation,
    ExternalActor,
    InboundMessage,
)

from opsmesh.gateway.adapters.dingtalk.client import DingTalkClient


async def parse_event(
    data: dict[str, Any], client: DingTalkClient, media: ChannelMedia
) -> InboundMessage:
    staff = data.get("senderStaffId")
    sender = staff or data.get("senderId")
    corp = data.get("senderCorpId") or client.config.corp_id
    if not sender or not data.get("conversationId") or not data.get("msgId"):
        raise ValueError("DingTalk message lacks stable identity")
    if str(corp) != client.config.corp_id:
        raise ValueError("DingTalk enterprise differs from configured scope")
    content = []

    async def attachment(value: dict[str, Any], kind: str) -> None:
        code = value.get("downloadCode") or value.get("pictureDownloadCode")
        if not code:
            return
        result = await client.post(
            "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
            {"downloadCode": code, "robotCode": client.config.robot_code},
        )
        key, mime = await media.fetch(result["downloadUrl"])
        content.append(
            ContentPart(
                type=kind,
                media_key=key,
                mime_type=mime,
                filename=value.get("fileName") or "attachment",
            )
        )

    msgtype = data.get("msgtype")
    if msgtype == "text":
        content.append(ContentPart(text=str(data.get("text", {}).get("content", "")).strip()))
    elif msgtype == "richText":
        for item in data.get("content", {}).get("richText", []):
            if item.get("text"):
                content.append(ContentPart(text=str(item["text"])))
            elif item.get("type") == "picture":
                await attachment(item, "image")
    elif msgtype in {"picture", "file", "audio", "video"}:
        await attachment(data.get("content", {}), {"picture": "image"}.get(msgtype, msgtype))
    if not content:
        content.append(ContentPart(text="[Unsupported message type. Send text or an attachment.]"))
    group = str(data.get("conversationType")) == "2"
    return InboundMessage(
        event_id=str(data["msgId"]),
        actor=ExternalActor(
            scope=client.config.app_key + ":" + str(corp),
            id_kind="staff_id" if staff else "sender_id",
            external_id=str(sender),
            name=str(data.get("senderNick") or ""),
            external_role="admin" if data.get("isAdmin") is True else None,
        ),
        conversation=Conversation(
            external_id=str(data["conversationId"]),
            kind="group" if group else "private",
            name=str(data.get("conversationTitle") or ""),
        ),
        content=content,
        reply_address={
            "webhook": data.get("sessionWebhook"),
            "expires_at": data.get("sessionWebhookExpiredTime"),
            "kind": "group" if group else "private",
            "target": str(data["conversationId"]),
            "staff_id": str(staff or ""),
            "msg_id": str(data["msgId"]),
        },
    )
