"""Normalize QQ rich media, voice transcripts and attached message context."""

import re
from datetime import datetime
from typing import Any

from opsmesh_sdk.channels import ChannelMedia, ContentPart, ContextMessage

_MEDIA = re.compile(
    r"<qq(img|video|audio|file)\s+[^>]*?(?:src|url)\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_MENTION = re.compile(r"<@!?[A-Za-z0-9_-]+>\s*")


def scene_indexes(data: dict[str, Any]) -> tuple[str, str]:
    scene = data.get("message_scene") or {}
    values = {}
    for entry in scene.get("ext", []):
        if isinstance(entry, str) and "=" in entry:
            key, value = entry.split("=", 1)
            values[key] = value
    return values.get("msg_idx", ""), values.get("ref_msg_idx", "")


async def content_parts(
    data: dict[str, Any], media: ChannelMedia, *, mentioned: bool = False
) -> list[ContentPart]:
    text = str(data.get("content") or "")
    identified_bot = False
    for item in data.get("mentions", []):
        if item.get("is_you") is True and item.get("id"):
            identified_bot = True
            text = re.sub(rf"<@!?{re.escape(str(item['id']))}>\s*", "", text)
    if mentioned and not identified_bot:
        text = _MENTION.sub("", text, count=1) if _MENTION.match(text) else text
    attachments = list(data.get("attachments") or [])
    for match in _MEDIA.finditer(text):
        attachments.append(
            {
                "url": match[2],
                "content_type": {"img": "image"}.get(match[1].lower(), match[1].lower()),
            }
        )
    text = _MEDIA.sub("", text).strip()
    parts = [ContentPart(text=text)] if text else []
    seen = set()
    for item in attachments[:100]:
        mime = str(item.get("content_type") or "")
        transcript = str(item.get("asr_refer_text") or "").strip()
        voice = mime == "voice" or mime.startswith("audio/") or bool(item.get("voice_wav_url"))
        if voice and transcript:
            if transcript not in seen:
                parts.append(ContentPart(text=transcript))
                seen.add(transcript)
            continue
        url = str((item.get("voice_wav_url") if voice else None) or item.get("url") or "")
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        if url in seen:
            continue
        seen.add(url)
        key, detected = await media.fetch(url)
        family = detected.split("/")[0]
        parts.append(
            ContentPart(
                type=family if family in {"image", "audio", "video"} else "file",
                media_key=key,
                mime_type=detected,
                filename=str(
                    item.get("filename")
                    or item.get("file_name")
                    or item.get("name")
                    or "attachment"
                ),
            )
        )
    return parts[:100]


def timestamp(value: object) -> float | None:
    try:
        if isinstance(value, (float, int)):
            result = float(value)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
            result = parsed.timestamp()
        else:
            return None
        return result if 0 <= result < 253402300800 else None
    except (ValueError, OverflowError):
        return None


async def attached_context(
    data: dict[str, Any], media: ChannelMedia
) -> tuple[list[ContextMessage], list[ContentPart]]:
    own, reference = scene_indexes(data)
    recent, quoted = [], []
    for element in (data.get("msg_elements") or [])[:100]:
        index = str(element.get("msg_idx") or "")
        if index and index == own:
            continue
        if reference and index != reference:
            continue
        parts = await content_parts(element, media)
        if not parts:
            continue
        if reference:
            quoted = [ContentPart(text="Quoted message:"), *parts]
            break
        author = element.get("author") or {}
        sender = author.get("member_openid") or author.get("user_openid") or author.get("id")
        sent = timestamp(element.get("timestamp"))
        identifier = str(element.get("id") or index)
        if sender and identifier and sent is not None:
            recent.append(
                ContextMessage(
                    message_id=identifier,
                    sender_id=str(sender),
                    sender_name=str(author.get("username") or ""),
                    timestamp=sent,
                    content=parts,
                )
            )
    return recent, quoted
