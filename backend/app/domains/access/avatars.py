"""Bounded, metadata-free account thumbnails; never workspace file objects."""

from base64 import b64decode
from binascii import Error as Base64Error
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.app.core.errors import DomainError


def normalize_avatar(encoded: str) -> bytes:
    try:
        if len(encoded) > 2796204:
            raise ValueError("Encoded image is too large")
        content = b64decode(encoded, validate=True)
        if not content or len(content) > 2 * 1024 * 1024:
            raise ValueError("Image is too large")
        with Image.open(BytesIO(content), formats=["JPEG", "PNG", "WEBP"]) as source:
            if max(source.size) > 4096 or getattr(source, "is_animated", False):
                raise ValueError("Unsupported image dimensions or animation")
            source.load()
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            normalized.thumbnail((256, 256), Image.Resampling.LANCZOS)
            output = BytesIO()
            # A fresh image drops EXIF, comments, profiles and all source metadata.
            clean = Image.new("RGBA", normalized.size)
            clean.paste(normalized)
            clean.save(output, format="WEBP", quality=85, method=4)
        result = output.getvalue()
        if len(result) > 262144:
            raise ValueError("Thumbnail is too large")
        return result
    except (
        Base64Error,
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise DomainError("Invalid avatar image", code="invalid_avatar") from exc
