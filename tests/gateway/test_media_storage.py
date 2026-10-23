"""Attachments work on a freshly initialized local storage root."""

import os

import pytest

from opsmesh.gateway.messaging.media import GatewayMedia


@pytest.mark.skipif(os.name != "posix", reason="Local storage requires POSIX directory descriptors")
@pytest.mark.asyncio
async def test_first_attachment_creates_storage_directory_and_roundtrips(tmp_path):
    media = GatewayMedia(tmp_path / "media")
    first = await media.save_bytes(b"first")
    second = await media.save_bytes(b"second")
    assert first != second
    assert await media.read(first) == b"first"
    assert await media.read(second) == b"second"
