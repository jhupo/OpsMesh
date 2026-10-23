"""Bounded attachments use the resources storage interface and public egress."""

import asyncio
import socket
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from opsmesh.resources.storage.base import FileStorage
from opsmesh.resources.storage.local import LocalFileStorage
from opsmesh.shared.security.url_validation import (
    EgressURLPolicy,
    resolve_egress_address,
    validate_egress_url,
)


class PublicResolver(AbstractResolver):
    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        address = await asyncio.to_thread(
            resolve_egress_address,
            "https://" + host,
            policy=EgressURLPolicy(frozenset({"https"}), allow_unresolved_hosts=False),
        )
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
        ]

    async def close(self) -> None:
        return None


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
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Attachment requires a public HTTPS URL")
        # aiohttp bypasses the resolver for literal IPs; validate those as well.
        await asyncio.to_thread(
            validate_egress_url,
            url,
            policy=EgressURLPolicy(frozenset({"https"}), allow_unresolved_hosts=False),
        )
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
        async with (
            aiohttp.ClientSession(
                connector=connector, trust_env=False, timeout=aiohttp.ClientTimeout(total=30)
            ) as client,
            client.get(url, allow_redirects=False) as response,
        ):
            response.raise_for_status()
            if response.status != 200:
                raise ValueError("Attachment redirects are not followed")
            result = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                result.extend(chunk)
                if len(result) > self.max_bytes:
                    raise ValueError("Attachment exceeds transfer limit")
            return await self.save_bytes(bytes(result)), response.headers.get(
                "Content-Type", "application/octet-stream"
            ).split(";")[0]
