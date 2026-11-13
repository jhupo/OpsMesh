"""Decode DingTalk media and exchange download codes asynchronously."""

import mimetypes
from functools import partial
from typing import Any

from ..attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
    AttachmentSettings,
)
from ..attachments.download import download
from ..attachments.transfer import AttachmentReader
from ..messages import Message, MessageSegment
from .client import DingTalkClient
from .config import DingTalkConnectionSettings


async def unavailable() -> bytes:
    """Reject media access outside a live channel."""
    raise AttachmentError("Attachment transport is not available")


def parse_message(
    payload: dict[str, Any],
    files: AttachmentCollection,
    reader: AttachmentReader | None,
) -> Message | None:
    """Preserve text and media order, including rich-text image blocks."""
    kind = payload.get("msgtype")
    if kind in {"text", "markdown"}:
        body = payload.get(kind, {})
        if not isinstance(body, dict):
            raise AttachmentError("Invalid DingTalk text content")
        content = body.get("content" if kind == "text" else "text")
        if not isinstance(content, str):
            raise AttachmentError("Invalid DingTalk text content")
        return Message(content)
    body = payload.get("content", {})
    if not isinstance(body, dict):
        raise AttachmentError("Invalid DingTalk media content")
    if kind == "richText":
        blocks = body.get("richText", [])
    elif kind in {"picture", "file", "audio", "video"}:
        blocks = [body]
    else:
        return None
    if not isinstance(blocks, list):
        raise AttachmentError("Invalid DingTalk rich text")
    message = Message()
    for block in blocks:
        if not isinstance(block, dict):
            raise AttachmentError("Invalid DingTalk message block")
        if isinstance(block.get("text"), str):
            message.append(MessageSegment.text(block["text"]))
            continue
        code = block.get("downloadCode") or block.get("pictureDownloadCode")
        if not isinstance(code, str) or not code:
            raise AttachmentError("Missing DingTalk download code")
        media_type = {
            "picture": "image/jpeg",
            "richText": "image/jpeg",
            "audio": "audio/ogg",
            "video": "video/mp4",
        }.get(kind, "application/octet-stream")
        if kind == "file":
            media_type = (
                mimetypes.guess_type(str(block.get("fileName", "")))[0]
                or "application/octet-stream"
            )
        metadata = Attachment.create(
            block.get("fileName", "attachment"),
            media_type,
            block.get("fileSize"),
        )
        files.add(metadata, partial(reader, code) if reader else unavailable)
        message.append(MessageSegment.attachment(metadata))
    return message


class DingTalkAttachments:
    """Use the connection's credentials only at official API endpoints."""

    def __init__(
        self,
        client: DingTalkClient,
        connection: DingTalkConnectionSettings,
        settings: AttachmentSettings,
    ) -> None:
        """Borrow the transport and trusted connection configuration."""
        self.client = client
        self.connection = connection
        self.settings = settings

    async def read(self, code: str) -> bytes:
        """Resolve a signed media link without exposing access tokens."""
        access_token = await self.client.access_token(self.connection)
        result = await self.client.post_json(
            "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
            {"downloadCode": code, "robotCode": self.connection.client_id},
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        url = result.get("downloadUrl")
        if not isinstance(url, str):
            raise AttachmentError("DingTalk download URL is unavailable")
        return await download(
            url,
            self.settings,
            ("dingtalk.com", "alicdn.com", "aliyuncs.com", "ddurl.cn"),
        )
