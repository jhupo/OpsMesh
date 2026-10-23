"""Avatar validation and normalization: bounded bytes stored on the profile.

Avatars are user profile data, not file resources: the normalized image is
kept as a bounded binary column instead of creating a second content store.
Normalization strips metadata, bounds the pixel count and re-encodes to one
canonical format so no uploaded payload reaches the database unchanged.
"""

from __future__ import annotations

import io
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageOps

from opsmesh.users.errors import InvalidAvatarError

MAX_AVATAR_UPLOAD_BYTES: Final = 2 * 1024 * 1024
MAX_AVATAR_BYTES: Final = 64 * 1024
MAX_AVATAR_DIMENSION: Final = 256
MIN_AVATAR_DIMENSION: Final = 64
MAX_AVATAR_PIXELS: Final = MAX_AVATAR_DIMENSION * MAX_AVATAR_DIMENSION * 16
AVATAR_CONTENT_TYPE: Final = "image/png"
ALLOWED_AVATAR_TYPES: Final = frozenset({"image/png", "image/jpeg", "image/webp"})
_DECLARED_FORMATS: Final = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


@dataclass(frozen=True)
class NormalizedAvatar:
    """The stored form of one avatar: canonical bytes and their content type."""

    content: bytes
    content_type: str
    width: int
    height: int


def normalize_avatar(content: bytes, *, content_type: str) -> NormalizedAvatar:
    """Validate and normalize one uploaded avatar.

    The declared content type must match the decoded image; anything that
    cannot be decoded, exceeds the bounds or is not an allowed image format
    is rejected before any byte is stored.
    """
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise InvalidAvatarError(
            "The avatar must be a PNG, JPEG or WebP image",
            details={"allowed_types": sorted(ALLOWED_AVATAR_TYPES)},
        )
    if not content:
        raise InvalidAvatarError("The avatar is empty")
    if len(content) > MAX_AVATAR_UPLOAD_BYTES:
        raise InvalidAvatarError(
            "The avatar exceeds the upload size limit",
            details={"max_upload_bytes": MAX_AVATAR_UPLOAD_BYTES},
        )
    with ExitStack() as images:
        try:
            image = images.enter_context(Image.open(io.BytesIO(content)))
            if image.format != _DECLARED_FORMATS[content_type]:
                raise InvalidAvatarError(
                    "The declared content type does not match the image",
                    details={"declared_type": content_type, "detected_format": image.format},
                )
            if image.width * image.height > MAX_AVATAR_PIXELS:
                raise InvalidAvatarError(
                    "The avatar image has too many pixels",
                    details={"max_pixels": MAX_AVATAR_PIXELS},
                )
            image.load()
            oriented = images.enter_context(ImageOps.exif_transpose(image))
            frame = oriented.convert("RGBA")
        except InvalidAvatarError:
            raise
        except Exception as exc:
            raise InvalidAvatarError("The avatar image is not decodable") from exc
    try:
        return _encode_avatar(frame)
    finally:
        frame.close()


def _encode_avatar(frame: Image.Image) -> NormalizedAvatar:
    side = MAX_AVATAR_DIMENSION
    while True:
        with frame.copy() as resized:
            resized.thumbnail((side, side), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="PNG", optimize=True)
            payload = buffer.getvalue()
            if len(payload) <= MAX_AVATAR_BYTES or side <= MIN_AVATAR_DIMENSION:
                return NormalizedAvatar(
                    content=payload,
                    content_type=AVATAR_CONTENT_TYPE,
                    width=resized.width,
                    height=resized.height,
                )
        side = max(MIN_AVATAR_DIMENSION, side // 2)
