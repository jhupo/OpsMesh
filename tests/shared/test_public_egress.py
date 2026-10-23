"""Outbound connections reject private destinations, including redirects and DNS changes."""

import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from opsmesh.shared.http import egress
from opsmesh.shared.security.url_validation import EgressURLValidationError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/file",
        "https://[::1]/file",
        "https://169.254.169.254/file",
        "https://10.0.0.1/file",
        "http://example.com/file",
        "https://user:secret@example.com/file",
    ],
)
async def test_private_or_insecure_url_never_reaches_transport(monkeypatch, url):
    connect = AsyncMock()
    monkeypatch.setattr(egress.aiohttp.ClientSession, "_request", connect)
    with pytest.raises(EgressURLValidationError):
        async with egress.public_https_response(url):
            pytest.fail("Unsafe URL reached response handling")
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_redirect_to_metadata_is_rejected_before_second_connection(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))
        ],
    )
    requests = []

    class Redirect:
        status = 302
        headers = {"Location": "https://169.254.169.254/credentials"}

    @asynccontextmanager
    async def get(self, url, **kwargs):
        requests.append(url)
        yield Redirect()

    monkeypatch.setattr(egress.aiohttp.ClientSession, "get", get)
    with pytest.raises(EgressURLValidationError):
        async with egress.public_https_response("https://example.com/plugin.zip"):
            pytest.fail("Unsafe redirect reached response handling")
    assert requests == ["https://example.com/plugin.zip"]


@pytest.mark.asyncio
async def test_connection_time_resolver_rejects_dns_rebinding(monkeypatch):
    addresses = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (next(addresses), 443))
        ],
    )
    egress.validate_egress_url("https://example.com/plugin.zip", policy=egress.POLICY)
    with pytest.raises(EgressURLValidationError):
        await egress.PublicResolver().resolve("example.com", 443)
