"""Convert QQ attachment metadata and fetch trusted CDN content."""

from functools import partial
from typing import Any

from ..attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
    AttachmentSettings,
)
from ..attachments.download import download
from ..attachments.transfer import AttachmentLoader, AttachmentReader
from ..messages import Message, MessageSegment


async def unavailable() -> bytes:
    """Reject media access outside a live channel."""
    raise AttachmentError("Attachment transport is not available")


def parse_message(
    data: dict[str, Any],
    files: AttachmentCollection,
    reader: AttachmentReader | None,
) -> Message:
    """Retain text followed by QQ's ordered attachment list."""
    message = Message(data.get("content", ""))
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list):
        raise AttachmentError("Invalid QQ attachments")
    for item in attachments:
        if not isinstance(item, dict):
            raise AttachmentError("Invalid QQ attachment")
        url = item.get("url")
        if not isinstance(url, str):
            raise AttachmentError("Missing QQ attachment URL")
        if url.startswith("//"):
            url = "https:" + url
        metadata = Attachment.create(
            item.get("filename", "attachment"),
            item.get("content_type", "application/octet-stream"),
            item.get("size"),
        )
        loader: AttachmentLoader = (
            partial(reader, url) if reader else unavailable
        )
        files.add(metadata, loader)
        message.append(MessageSegment.attachment(metadata))
    return message


async def read(url: str, settings: AttachmentSettings) -> bytes:
    """Fetch one QQ media object under configured size limits."""
    return await download(url, settings, ("qq.com", "qpic.cn", "gtimg.cn"))
