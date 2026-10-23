"""Replies respect expiring webhook credentials; proactive sends use OpenAPI."""

import json
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from opsmesh_sdk.channels import ChannelMedia, DeliveryReceipt, DeliveryRejected, OutboundMessage

from opsmesh.gateway.adapters.dingtalk.client import DingTalkClient


async def send_message(
    client: DingTalkClient, media: ChannelMedia, message: OutboundMessage
) -> DeliveryReceipt:
    if len(message.content) != 1:
        raise DeliveryRejected("DingTalk delivery must contain one part")
    part, address = message.content[0], message.address
    webhook = address.get("webhook")
    if part.type == "text" and webhook and int(address.get("expires_at") or 0) > time.time() * 1000:
        parsed = urlparse(webhook)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "oapi.dingtalk.com"
            or parsed.username
            or parsed.port not in {None, 443}
        ):
            raise DeliveryRejected("Invalid DingTalk reply address")
        value = await client.post(
            webhook,
            {"msgtype": "text", "text": {"content": part.text}},
            authenticated=False,
            sending=True,
        )
    else:
        if part.type == "text":
            key, param = "sampleText", {"content": part.text}
        elif part.media_key and part.type in {"image", "file", "audio"}:
            token = await client.access_token()
            response = await client.http.post(
                "https://oapi.dingtalk.com/media/upload",
                params={
                    "access_token": token,
                    "type": {"image": "image", "audio": "voice", "file": "file"}[part.type],
                },
                files={"media": (part.filename, await media.read(part.media_key), part.mime_type)},
            )
            if response.status_code != 200 or response.json().get("errcode", 0):
                raise DeliveryRejected("DingTalk media upload rejected")
            media_id = response.json()["media_id"]
            if part.type == "image":
                key, param = "sampleImageMsg", {"photoURL": media_id}
            elif part.type == "audio":
                key, param = "sampleAudio", {"mediaId": media_id}
            else:
                key, param = (
                    "sampleFile",
                    {
                        "mediaId": media_id,
                        "fileName": part.filename,
                        "fileType": PurePosixPath(part.filename).suffix.lstrip(".") or "bin",
                    },
                )
        else:
            raise DeliveryRejected("DingTalk message type is unsupported")
        body: dict[str, Any] = {
            "robotCode": client.config.robot_code,
            "msgKey": key,
            "msgParam": json.dumps(param, ensure_ascii=False),
        }
        if address["kind"] == "group":
            body["openConversationId"] = address["target"]
            path = "groupMessages/send"
        elif address.get("staff_id"):
            body["userIds"] = [address["staff_id"]]
            path = "oToMessages/batchSend"
        else:
            raise DeliveryRejected("No unexpired webhook or verified staff ID")
        value = await client.post("https://api.dingtalk.com/v1.0/robot/" + path, body, sending=True)
        if value.get("invalidStaffIdList") or value.get("flowControlledStaffIdList"):
            raise DeliveryRejected("DingTalk recipient rejected")
    return DeliveryReceipt(
        remote_id=str(value.get("processQueryKey") or value.get("requestId") or "")
    )
