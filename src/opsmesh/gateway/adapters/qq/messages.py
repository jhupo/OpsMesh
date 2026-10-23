"""One persisted delivery maps to one QQ message send."""

import base64
from urllib.parse import quote

from opsmesh_sdk.channels import ChannelMedia, DeliveryReceipt, DeliveryRejected, OutboundMessage

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
    if kind not in paths or len(message.content) != 1:
        raise DeliveryRejected("Unsupported QQ delivery")
    part = message.content[0]
    body = {"msg_id": address.get("msg_id"), "msg_seq": address.get("msg_seq", 1)}
    if part.type == "text":
        body.update(content=part.text, msg_type=0)
    elif kind in {"c2c", "group"} and part.type in {"image", "audio", "video"} and part.media_key:
        data = await media.read(part.media_key)
        files_path = paths[kind].removesuffix("messages") + "files"
        uploaded = await client.request(
            "POST",
            files_path,
            {
                "file_type": {"image": 1, "video": 2, "audio": 3}[part.type],
                "file_data": base64.b64encode(data).decode(),
                "srv_send_msg": False,
            },
        )
        body.update(msg_type=7, media={"file_info": uploaded["file_info"]})
    else:
        raise DeliveryRejected("QQ media type is not supported for this destination")
    value = await client.request("POST", paths[kind], body, sending=True)
    return DeliveryReceipt(remote_id=str(value.get("id") or ""))
