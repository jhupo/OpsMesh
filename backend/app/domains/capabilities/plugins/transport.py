"""Bounded data-only HTTPS downloads; DNS is resolved once and the connection is pinned."""

import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx

from backend.app.core.security.egress import validate_url_shape


def validate_distribution_url(url: str, allowed_hosts: list[str]) -> str:
    validate_url_shape(url, allowed_schemes=frozenset({"https"}))
    parsed = urlsplit(url)
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    if parsed.port not in (None, 443) or host not in allowed_hosts:
        raise ValueError("Distribution host is not approved")
    if parsed.query:
        raise ValueError("Distribution URLs must not contain query parameters")
    return host


class PluginFetchError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PluginHttpFetcher:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def fetch(self, url: str, allowed_hosts: list[str], *, max_bytes: int) -> bytes:
        deadline = time.monotonic() + 30
        try:
            for _ in range(4):
                host = validate_distribution_url(url, allowed_hosts)
                addresses = {
                    ipaddress.ip_address(str(row[4][0]))
                    for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                }
                if not addresses or any(not address.is_global for address in addresses):
                    raise PluginFetchError("non_public_address")
                address = str(sorted(addresses, key=str)[0])
                # A fresh client prevents cross-host pool reuse. SNI and certificate validation
                # retain the approved hostname while the TCP connection uses the validated IP.
                with (
                    httpx.Client(
                        timeout=5, trust_env=False, follow_redirects=False, transport=self.transport
                    ) as client,
                    client.stream(
                        "GET",
                        httpx.URL(url).copy_with(host=address),
                        headers={"Host": host, "Accept-Encoding": "identity"},
                        extensions={"sni_hostname": host},
                    ) as response,
                ):
                    if time.monotonic() > deadline:
                        raise PluginFetchError("download_timeout")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise PluginFetchError("http_status")
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise PluginFetchError("encoded_response")
                    content = bytearray()
                    for chunk in response.iter_raw():
                        if time.monotonic() > deadline:
                            raise PluginFetchError("download_timeout")
                        if len(content) + len(chunk) > max_bytes:
                            raise PluginFetchError("download_too_large")
                        content.extend(chunk)
                    return bytes(content)
            raise PluginFetchError("redirect_limit")
        except (httpx.HTTPError, OSError) as exc:
            raise PluginFetchError("network_error") from exc
        except ValueError as exc:
            raise PluginFetchError("invalid_distribution_url") from exc
