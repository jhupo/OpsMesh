"""Bounded attachments use the resources storage interface and public egress."""

import asyncio
import base64
from pathlib import Path
from uuid import uuid4

from opsmesh_sdk.channels import ContentPart

from opsmesh.resources.storage.base import FileStorage
from opsmesh.resources.storage.local import LocalFileStorage
from opsmesh.shared.http.egress import public_https_response


class GatewayMedia:
    def __init__(
        self, root: Path, *, storage: FileStorage | None = None, max_bytes: int = 8_000_000
    ) -> None:
        self.storage = storage or LocalFileStorage(root, max_transfer_bytes=max_bytes)
        self.max_bytes = max_bytes

    async def save_bytes(self, data: bytes) -> str:
        if len(data) > self.max_bytes:
            raise ValueError("Gateway media exceeds transfer limit")
        key = uuid4().hex
        await asyncio.to_thread(self.storage.ensure_directory, "attachments")
        await asyncio.to_thread(self.storage.write_file, "attachments", path=key, content=data)
        return key

    async def read(self, key: str) -> bytes:
        if len(key) != 32 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid gateway media key")
        return await asyncio.to_thread(self.storage.read_file, "attachments", path=key)

    async def fetch(self, url: str) -> tuple[str, str]:
        async with public_https_response(url, timeout=30, max_redirects=0) as response:
            result = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                result.extend(chunk)
                if len(result) > self.max_bytes:
                    raise ValueError("Attachment exceeds transfer limit")
            return await self.save_bytes(bytes(result)), response.headers.get(
                "Content-Type", "application/octet-stream"
            ).split(";")[0]

    async def prepare(
        self, content: list[ContentPart], *, preserve_image_url: bool = False
    ) -> list[ContentPart]:
        result = []
        for part in content:
            if part.url is not None:
                key, mime = await self.fetch(part.url)
                if not (preserve_image_url and part.type == "image"):
                    part = part.model_copy(
                        update={"media_key": key, "mime_type": mime, "url": None}
                    )
            elif part.data is not None:
                key = await self.save_bytes(base64.b64decode(part.data, validate=True))
                part = part.model_copy(update={"media_key": key, "data": None})
            result.append(part)
        return result
