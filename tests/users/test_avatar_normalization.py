"""Avatar normalization tests: format admission, bounds, metadata stripping and size."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from opsmesh.users.avatars import (
    AVATAR_CONTENT_TYPE,
    MAX_AVATAR_BYTES,
    MAX_AVATAR_DIMENSION,
    MAX_AVATAR_PIXELS,
    normalize_avatar,
)
from opsmesh.users.errors import InvalidAvatarError


def encode(image: Image.Image, *, image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def solid(size: tuple[int, int], color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", size, color)


def test_a_small_png_is_normalized_to_the_canonical_format() -> None:
    content = encode(solid((64, 64), (255, 0, 0, 255)))
    normalized = normalize_avatar(content, content_type="image/png")
    assert normalized.content_type == AVATAR_CONTENT_TYPE
    assert normalized.width == 64
    assert normalized.height == 64
    assert len(normalized.content) <= MAX_AVATAR_BYTES
    assert Image.open(io.BytesIO(normalized.content)).format == "PNG"


def test_a_large_image_is_scaled_down_to_the_bound() -> None:
    content = encode(solid((512, 512), (0, 128, 255, 255)))
    normalized = normalize_avatar(content, content_type="image/png")
    assert max(normalized.width, normalized.height) <= MAX_AVATAR_DIMENSION
    assert len(normalized.content) <= MAX_AVATAR_BYTES


def test_a_jpeg_is_accepted_and_re_encoded_as_png() -> None:
    content = encode(solid((96, 96), (10, 20, 30, 255)).convert("RGB"), image_format="JPEG")
    normalized = normalize_avatar(content, content_type="image/jpeg")
    assert normalized.content_type == AVATAR_CONTENT_TYPE


def test_an_undeclared_content_type_is_rejected() -> None:
    content = encode(solid((64, 64), (1, 2, 3, 255)))
    with pytest.raises(InvalidAvatarError):
        normalize_avatar(content, content_type="image/gif")


def test_a_mismatched_declaration_is_rejected() -> None:
    png = encode(solid((64, 64), (1, 2, 3, 255)))
    with pytest.raises(InvalidAvatarError):
        normalize_avatar(png, content_type="image/jpeg")


def test_an_undecodable_payload_is_rejected() -> None:
    with pytest.raises(InvalidAvatarError):
        normalize_avatar(b"not-an-image", content_type="image/png")


def test_an_empty_payload_is_rejected() -> None:
    with pytest.raises(InvalidAvatarError):
        normalize_avatar(b"", content_type="image/png")


def test_an_oversized_upload_is_rejected() -> None:
    content = b"\x00" * (2 * 1024 * 1024 + 1)
    with pytest.raises(InvalidAvatarError):
        normalize_avatar(content, content_type="image/png")


def test_pixel_bound_is_checked_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    side = int(MAX_AVATAR_PIXELS**0.5) + 1
    content = encode(solid((side, side), (0, 0, 0, 255)))

    def unexpected_decode(self: Image.Image) -> None:
        raise AssertionError("The pixel-bound image must be rejected before decode")

    monkeypatch.setattr(Image.Image, "load", unexpected_decode)
    with pytest.raises(InvalidAvatarError, match="too many pixels"):
        normalize_avatar(content, content_type="image/png")
