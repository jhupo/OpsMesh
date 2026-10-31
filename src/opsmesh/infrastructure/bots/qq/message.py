"""Text, Markdown and incoming attachment conversion for official QQ messages."""

import html
import re
from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, OpsMeshMessageSegment
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData


def decode_message(data: JsonData, *, self_id: str) -> OpsMeshMessage:
    content = data.get("content", "")
    if not isinstance(content, str):
        raise OpsMeshError("INVALID_REQUEST", "QQ message content must be text")
    content = re.sub(rf"^\s*<@!?{re.escape(self_id)}>\s*", "", html.unescape(content))
    segments = [OpsMeshMessageSegment.text(content)] if content else []
    attachments = data.get("attachments") or []
    if not isinstance(attachments, list):
        raise OpsMeshError("INVALID_REQUEST", "QQ message attachments must be a list")
    for item in attachments:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise OpsMeshError("INVALID_REQUEST", "QQ message attachment is invalid")
        url = item["url"]
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("https://", "http://")):
            raise OpsMeshError("INVALID_REQUEST", "QQ attachment must use an HTTP(S) URL")
        mime = str(item.get("content_type", ""))
        if mime.startswith("image/"):
            segments.append(OpsMeshMessageSegment.image(url))
        else:
            name = item.get("filename")
            segments.append(
                OpsMeshMessageSegment.file(url, name=name if isinstance(name, str) else None)
            )
    return OpsMeshMessage(segments)


def encode_message(message: OpsMeshMessage, *, typed: bool) -> dict[str, Any]:
    if message.segments and all(segment.type == "text" for segment in message):
        content = message.extract_plain_text()
        if not content:
            raise OpsMeshError("INVALID_REQUEST", "QQ text message cannot be empty")
        return {"content": content, **({"msg_type": 0} if typed else {})}
    if len(message.segments) == 1 and message.segments[0].type in {"markdown", "qq:markdown"}:
        markdown_content = message.segments[0].data.get("content")
        if not isinstance(markdown_content, str) or not markdown_content:
            raise OpsMeshError("INVALID_REQUEST", "QQ Markdown requires nonempty content")
        return {"markdown": {"content": markdown_content}, **({"msg_type": 2} if typed else {})}
    raise OpsMeshError(
        "UNSUPPORTED_FEATURE", "QQ supports text or one Markdown segment for sending"
    )
