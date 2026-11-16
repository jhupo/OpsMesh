"""Upload bounded local media through QQ's official rich-media protocol."""

import base64
from enum import IntEnum

from qqbot_agent_sdk import QQApiClient
from qqbot_agent_sdk.dto import RichMediaMessage

from ..attachments import AttachmentError
from ..sending import OutgoingAttachment
from .errors import QQSendError


class MediaType(IntEnum):
    """QQ OpenAPI rich-media file_type values."""

    IMAGE = 1
    VIDEO = 2
    AUDIO = 3
    FILE = 4


def media_type(part: OutgoingAttachment) -> MediaType:
    """Reject formats that need transcoding before attempting upload."""
    mime = part.attachment.media_type
    if part.kind == "image":
        if mime == "image/png" and part.content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            return MediaType.IMAGE
        if mime == "image/jpeg" and part.content.startswith(b"\xff\xd8\xff"):
            return MediaType.IMAGE
        raise AttachmentError("QQ images require PNG or JPEG content")
    if part.kind == "video":
        if mime != "video/mp4" or part.content[4:8] != b"ftyp":
            raise AttachmentError("QQ videos require MP4 content")
        return MediaType.VIDEO
    if part.kind == "audio":
        if not part.content.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
            raise AttachmentError("QQ voice replies require SILK content")
        return MediaType.AUDIO
    return MediaType.FILE


class QQMedia:
    """Upload into one authenticated recipient's media scope."""

    def __init__(
        self, client: QQApiClient, recipient: tuple[str, str]
    ) -> None:
        """Bind the native API to a validated group or private recipient."""
        self.client = client
        self.scope, self.recipient = recipient

    async def upload(self, part: OutgoingAttachment) -> str:
        """Upload already bounded bytes with the SDK's native media payload."""
        body = RichMediaMessage(
            file_type=int(media_type(part)),
            file_data=base64.b64encode(part.content).decode("ascii"),
            file_name=part.attachment.name,
            srv_send_msg=False,
        )
        upload = (
            self.client.upload_group_file
            if self.scope == "group"
            else self.client.upload_c2c_file
        )
        response = await upload(self.recipient, body)
        file_info = response.get("file_info")
        if not isinstance(file_info, str) or not file_info:
            raise QQSendError("QQ upload response is missing file_info")
        return file_info
