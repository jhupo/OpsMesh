"""Public HTTPS downloads with address validation at the connection boundary."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from opsmesh.shared.security.url_validation import (
    EgressURLPolicy,
    resolve_egress_address,
    validate_egress_url,
)

POLICY = EgressURLPolicy(frozenset({"https"}), allow_unresolved_hosts=False)


class PublicResolver(AbstractResolver):
    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        address = await asyncio.to_thread(resolve_egress_address, "https://" + host, policy=POLICY)
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


@asynccontextmanager
async def public_https_response(
    url: str, *, timeout: float = 60, max_redirects: int = 5
) -> AsyncIterator[aiohttp.ClientResponse]:
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(
        connector=connector, trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as client:
        for hop in range(max_redirects + 1):
            # Literal IPs bypass aiohttp's resolver; check every redirect before connecting.
            await asyncio.to_thread(validate_egress_url, url, policy=POLICY)
            if urlsplit(url).port not in {None, 443}:
                raise ValueError("Public downloads require HTTPS port 443")
            async with client.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if hop == max_redirects or not response.headers.get("Location"):
                        raise ValueError("Public download redirect limit exceeded")
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                if response.status != 200:
                    raise ValueError("Public download requires a complete response")
                yield response
                return
