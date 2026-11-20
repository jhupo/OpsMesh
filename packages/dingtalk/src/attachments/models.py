"""Public attachment metadata and bounded transfer settings."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AttachmentError(ValueError):
    """An attachment cannot be safely resolved or transferred."""


class AttachmentSettings(BaseModel):
    """Bound individual files, complete messages, and upload caches."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_file_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    max_message_bytes: int = Field(default=24 * 1024 * 1024, gt=0)
    max_files: int = Field(default=8, ge=1, le=100)
    max_cache_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_cache_entries: int = Field(default=256, gt=0)
    ttl_seconds: float = Field(default=900, gt=0)
    download_timeout: float = Field(default=30, gt=0)


@dataclass(frozen=True)
class Attachment:
    """An opaque reference; never a vendor credential or local path."""

    id: str
    name: str
    media_type: str
    size: int | None = None

    @classmethod
    def create(
        cls, name: str, media_type: str, size: int | None = None
    ) -> "Attachment":
        """Validate display metadata independently of the storage name."""
        if not isinstance(name, str) or not isinstance(media_type, str):
            raise AttachmentError("Invalid attachment metadata")
        name = PurePosixPath(name.replace("\\", "/")).name
        if not name or name in {".", ".."} or len(name) > 255:
            raise AttachmentError("Invalid attachment name")
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise AttachmentError("Invalid attachment name")
        media_type = media_type.split(";", 1)[0].strip().lower()
        if not re.fullmatch(
            r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type
        ):
            raise AttachmentError("Invalid attachment media type")
        if size is not None and (type(size) is not int or size < 0):
            raise AttachmentError("Invalid attachment size")
        return cls(uuid4().hex, name, media_type, size)
