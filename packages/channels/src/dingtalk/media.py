"""DingTalk upload formats and native robot message templates."""

from pathlib import PurePosixPath

from ..attachments import AttachmentError
from ..sending import OutgoingAttachment
from .client import DingTalkClient


def validate(part: OutgoingAttachment) -> None:
    """Check required media attributes before uploading or sending."""
    mime = part.attachment.media_type
    if part.kind == "image":
        valid = (
            mime == "image/png"
            and part.content.startswith(b"\x89PNG\r\n\x1a\n")
            or mime == "image/jpeg"
            and part.content.startswith(b"\xff\xd8\xff")
            or mime == "image/gif"
            and part.content.startswith((b"GIF87a", b"GIF89a"))
        )
        if not valid:
            raise AttachmentError(
                "DingTalk images require PNG, JPEG or GIF content"
            )
    elif part.kind == "audio":
        if not part.content.startswith(b"#!AMR\n"):
            raise AttachmentError("DingTalk voice replies require AMR content")
        if part.duration_ms is None:
            raise AttachmentError("DingTalk voice replies require duration_ms")
    elif part.kind == "video":
        if mime != "video/mp4" or part.content[4:8] != b"ftyp":
            raise AttachmentError("DingTalk videos require MP4 content")
        if part.duration_ms is None or part.thumbnail is None:
            raise AttachmentError(
                "DingTalk videos require duration_ms and a thumbnail"
            )
        validate(
            OutgoingAttachment(
                part.thumbnail, part.thumbnail_content or b"", "image"
            )
        )
    elif part.kind == "file":
        suffix = PurePosixPath(part.attachment.name).suffix[1:]
        if not suffix or not suffix.isascii() or not suffix.isalnum():
            raise AttachmentError("DingTalk files require a filename extension")


class DingTalkMedia:
    """Upload bounded event bytes without exposing tokens to plugins."""

    def __init__(self, client: DingTalkClient, token: str) -> None:
        """Borrow a token for this complete send operation."""
        self.client = client
        self.token = token

    async def upload(
        self, part: OutgoingAttachment
    ) -> tuple[str, dict[str, str]]:
        """Return the official msgKey and msgParam after materialization."""
        validate(part)
        media_id = await self.client.upload_media(
            self.token,
            part.attachment,
            part.content,
            kind="voice" if part.kind == "audio" else part.kind,
        )
        if part.kind == "image":
            return "sampleImageMsg", {"photoURL": media_id}
        if part.kind == "file":
            return "sampleFile", {
                "mediaId": media_id,
                "fileName": part.attachment.name,
                "fileType": PurePosixPath(part.attachment.name).suffix[1:],
            }
        if part.kind == "audio":
            return "sampleAudio", {
                "mediaId": media_id,
                "duration": str(part.duration_ms),
            }
        thumbnail = await self.client.upload_media(
            self.token,
            part.thumbnail,
            part.thumbnail_content,
            kind="image",
        )
        return "sampleVideo", {
            "videoMediaId": media_id,
            "videoType": "mp4",
            "duration": str(part.duration_ms),
            "picMediaId": thumbnail,
        }
