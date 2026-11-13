"""Translate admitted messages into native model inputs or sandbox files."""

import base64
import io
from pathlib import Path
from uuid import uuid4

from agents.items import TResponseInputItem
from agents.sandbox.session.sandbox_session import SandboxSession

from opsmesh.messages import Message
from opsmesh.plugins.channels.attachments import (
    AttachmentCollection,
    AttachmentError,
)


async def model_input(
    message: Message | str,
    files: AttachmentCollection,
) -> str | list[TResponseInputItem]:
    """Embed bounded media so native Session history survives URL expiry."""
    if isinstance(message, str):
        return message
    if all(segment.is_text() for segment in message):
        return message.extract_plain_text()
    content = []
    for segment in message:
        if segment.is_text():
            content.append({"type": "input_text", "text": str(segment)})
            continue
        if segment.type == "mention":
            continue
        if segment.type not in {"image", "file"}:
            raise AttachmentError(
                "This agent accepts images and documents only"
            )
        attachment = files.get(segment.data["attachment_id"])
        data = await files.read(attachment.id)
        encoded = base64.b64encode(data).decode("ascii")
        media_type = attachment.media_type
        if segment.type == "image":
            media_type = image_type(data)
            content.append(
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{media_type};base64,{encoded}",
                }
            )
        elif media_type == "application/pdf":
            if not data.startswith(b"%PDF-"):
                raise AttachmentError("Invalid PDF content")
            content.append(
                {
                    "type": "input_file",
                    "filename": attachment.name,
                    "file_data": f"data:application/pdf;base64,{encoded}",
                }
            )
        elif media_type.startswith("text/") or media_type == "application/json":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise AttachmentError(
                    "Text attachments must use UTF-8"
                ) from None
            content.append({"type": "input_text", "text": text})
        else:
            raise AttachmentError("Use the sandbox agent for this file type")
    if not content:
        raise AttachmentError("Message has no supported content")
    return [{"role": "user", "content": content}]


def image_type(data: bytes) -> str:
    """Verify supported image signatures instead of trusting a filename."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise AttachmentError("Unsupported or invalid image content")


async def sandbox_input(
    message: Message | str,
    files: AttachmentCollection,
    sandbox: SandboxSession,
) -> str:
    """Write attachments through the SDK into the current session workspace."""
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for segment in message:
        if segment.is_text():
            parts.append(str(segment))
        elif segment.type in {"image", "file", "audio", "video"}:
            attachment = files.get(segment.data["attachment_id"])
            content = await files.read(attachment.id)
            suffix = Path(attachment.name).suffix
            if not suffix.isascii() or not suffix[1:].isalnum():
                suffix = ""
            path = Path("attachment-" + uuid4().hex + suffix[:16])
            await sandbox.write(path, io.BytesIO(content))
            parts.append(
                f"User attachment {attachment.name!r} saved as {path}."
            )
        elif segment.type != "mention":
            raise AttachmentError("Unsupported message content")
    if not parts:
        raise AttachmentError("Message has no supported content")
    return "\n".join(parts)
