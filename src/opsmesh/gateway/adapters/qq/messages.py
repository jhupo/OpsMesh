"""One persisted delivery maps to one QQ message send."""

import base64
from urllib.parse import quote

from opsmesh_sdk.channels import (
    ChannelMedia,
    DeliveryReceipt,
    DeliveryRejected,
    DeliveryUncertain,
    OutboundMessage,
)

from opsmesh.gateway.adapters.qq.client import QQClient


async def send_message(
    client: QQClient, media: ChannelMedia, message: OutboundMessage
) -> DeliveryReceipt:
    address = message.address
    kind = address["kind"]
    target = quote(str(address["target"]), safe="")
    paths = {
        "c2c": f"/v2/users/{target}/messages",
        "group": f"/v2/groups/{target}/messages",
        "channel": f"/channels/{target}/messages",
        "dm": f"/dms/{target}/messages",
    }
    if message.event == "recall":
        if kind != "group" or message.content:
            raise DeliveryRejected("QQ recall requires a group message without content")
        message_id = address.get("msg_id")
        if not message_id:
            raise DeliveryRejected("QQ recall requires the original message ID")
        await client.request(
            "DELETE", paths[kind] + "/" + quote(str(message_id), safe=""), sending=True
        )
        return DeliveryReceipt(remote_id=str(message_id))
    if kind not in paths or len(message.content) != 1:
        raise DeliveryRejected("Unsupported QQ delivery")
    part = message.content[0]
    body = {"msg_seq": address.get("msg_seq", 1)}
    if address.get("msg_id"):
        body["msg_id"] = address["msg_id"]
    if message.event == "typing":
        if kind != "c2c":
            raise DeliveryRejected("QQ typing requires a private conversation")
        body.update(msg_type=6, input_notify={"input_type": 1, "input_second": 60})
        await client.request("POST", paths[kind], body, sending=True)
        return DeliveryReceipt(remote_id="typing")
    if message.event in {"delta", "completed"}:
        if kind != "c2c" or part.type != "text" or message.stream_id is None:
            raise DeliveryRejected("QQ replace streaming requires private text")
        stream_body = {
            "input_mode": "replace",
            "input_state": 10 if message.event == "completed" else 1,
            "content_type": "markdown",
            "content_raw": part.text,
            "msg_id": address.get("msg_id"),
            "event_id": address.get("msg_id"),
            "msg_seq": int(message.stream_id.replace("-", ""), 16) % 65536,
            "index": message.stream_index,
        }
        if address.get("stream_msg_id"):
            stream_body["stream_msg_id"] = address["stream_msg_id"]
        value = await client.request(
            "POST", f"/v2/users/{target}/stream_messages", stream_body, sending=True
        )
        remote_id = value.get("stream_msg_id") or value.get("id")
        if not remote_id and isinstance(value.get("data"), dict):
            remote_id = value["data"].get("stream_msg_id") or value["data"].get("id")
        if not remote_id:
            raise DeliveryUncertain("QQ did not confirm the stream identity")
        return DeliveryReceipt(remote_id=str(remote_id))
    if part.type == "text":
        body.update(content=part.text, msg_type=0)
    elif kind in {"channel", "dm"} and part.type == "image" and part.url:
        body.update(content="", image=part.url)
    elif kind in {"c2c", "group"} and part.media_key:
        data = await media.read(part.media_key)
        files_path = paths[kind].removesuffix("messages") + "files"
        uploaded = await client.request(
            "POST",
            files_path,
            {
                "file_type": {"image": 1, "video": 2, "audio": 3, "file": 4}[part.type],
                **({"file_name": part.filename} if part.type == "file" else {}),
                "file_data": base64.b64encode(data).decode(),
                "srv_send_msg": False,
            },
        )
        body.update(msg_type=7, media={"file_info": uploaded["file_info"]})
    elif kind in {"c2c", "group"} and part.url:
        uploaded = await client.request(
            "POST",
            paths[kind].removesuffix("messages") + "files",
            {
                "file_type": {"image": 1, "video": 2, "audio": 3, "file": 4}[part.type],
                "url": part.url,
                "srv_send_msg": False,
            },
        )
        body.update(msg_type=7, media={"file_info": uploaded["file_info"]})
    else:
        raise DeliveryRejected("QQ media type is not supported for this destination")
    value = await client.request("POST", paths[kind], body, sending=True)
    return DeliveryReceipt(remote_id=str(value.get("id") or ""))
