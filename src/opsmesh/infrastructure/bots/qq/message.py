"""Text, Markdown and incoming attachment conversion for official QQ messages."""

import html
import re
from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, OpsMeshMessageSegment
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData


def _text_segments(content: str, mentions: Any, self_id: str) -> list[OpsMeshMessageSegment]:
    if mentions is None:
        # Older @-event payloads have no structured mention list.
        text = re.sub(rf"^\s*<@!?{re.escape(self_id)}>\s*", "", html.unescape(content))
        return [OpsMeshMessageSegment.text(text)] if text else []
    if not isinstance(mentions, list) or len(mentions) > 1000:
        raise OpsMeshError("INVALID_REQUEST", "QQ mentions must be a bounded list")
    identifiers: dict[str, str] = {}
    for mention in mentions:
        if not isinstance(mention, dict):
            raise OpsMeshError("INVALID_REQUEST", "QQ mention must be a user object")
        aliases = {
            value
            for key in ("id", "member_openid", "user_openid")
            if isinstance(value := mention.get(key), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value)
        }
        for value in aliases:
            identifiers[value] = self_id if self_id in aliases else value
    segments, position = [], 0
    for match in re.finditer(r"<@!?([A-Za-z0-9_-]{1,256})>", content):
        identifier = identifiers.get(match.group(1))
        if identifier is None:
            continue
        if match.start() > position:
            segments.append(
                OpsMeshMessageSegment.text(html.unescape(content[position : match.start()]))
            )
        segments.append(OpsMeshMessageSegment(type="qq:mention", data={"user_id": identifier}))
        position = match.end()
    if position < len(content):
        segments.append(OpsMeshMessageSegment.text(html.unescape(content[position:])))
    return segments


def decode_message(data: JsonData, *, self_id: str) -> OpsMeshMessage:
    content = data.get("content", "")
    if not isinstance(content, str):
        raise OpsMeshError("INVALID_REQUEST", "QQ message content must be text")
    segments = _text_segments(content, data.get("mentions"), self_id)
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
        elif mime.startswith("audio/"):
            segments.append(OpsMeshMessageSegment.audio(url))
        elif mime.startswith("video/"):
            segments.append(OpsMeshMessageSegment.video(url))
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
