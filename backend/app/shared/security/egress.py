from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)


@dataclass(frozen=True)
class EgressUrlPolicy:
    allowed_schemes: frozenset[str]
    allow_unresolved_hosts: bool = True
    allow_private_addresses: bool = False


MCP_EGRESS_URL_POLICY = EgressUrlPolicy(
    allowed_schemes=frozenset({"http", "https"}),
)
MODEL_PROVIDER_BASE_URL_POLICY = EgressUrlPolicy(
    allowed_schemes=frozenset({"https"}),
)


class EgressUrlValidationError(ValueError):
    pass


def validate_egress_url(url: str, *, policy: EgressUrlPolicy) -> str:
    validate_url_shape(url, allowed_schemes=policy.allowed_schemes)
    parsed = urlparse(url)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EgressUrlValidationError("URL port is invalid") from exc

    hostname = parsed.hostname
    if hostname is None:
        raise EgressUrlValidationError("URL host is required")
    host = _normalized_hostname(hostname)
    if _is_blocked_hostname(host):
        raise EgressUrlValidationError("URL host is not allowed")

    literal_address = _ip_address(host)
    if literal_address is not None:
        _validate_address(literal_address, policy=policy)
        return url

    for address in _resolve_host(host, policy=policy):
        _validate_address(address, policy=policy)
    return url


def validate_url_shape(url: str, *, allowed_schemes: frozenset[str]) -> str:
    """Validate URL syntax and reject credential-bearing configuration values."""
    if not isinstance(url, str) or not url.strip():
        raise EgressUrlValidationError("URL is required")
    if any(ord(character) < 0x20 for character in url):
        raise EgressUrlValidationError("URL contains control characters")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in allowed_schemes:
        raise EgressUrlValidationError("URL scheme is not allowed")
    if not parsed.hostname:
        raise EgressUrlValidationError("URL host is required")
    if parsed.username is not None or parsed.password is not None:
        raise EgressUrlValidationError("URL credentials are not allowed")
    if parsed.fragment:
        raise EgressUrlValidationError("URL fragments are not allowed")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.strip().lower().replace("-", "_")
        if (
            normalized_key in _SENSITIVE_QUERY_KEYS
            or normalized_key.endswith("_token")
            or normalized_key.endswith("_secret")
        ):
            raise EgressUrlValidationError("URL query must not contain credentials")
    return url


def url_host(url: str) -> str | None:
    """Return a redaction-safe host and optional port without user information."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None
    rendered_hostname = f"[{hostname}]" if ":" in hostname else hostname
    return f"{rendered_hostname}:{port}" if port is not None else rendered_hostname


def _normalized_hostname(hostname: str) -> str:
    return hostname.strip().rstrip(".").lower()


def _is_blocked_hostname(hostname: str) -> bool:
    return hostname == "localhost" or hostname.endswith(".localhost")


def _ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _resolve_host(
    hostname: str,
    *,
    policy: EgressUrlPolicy,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if policy.allow_unresolved_hosts:
            return set()
        raise EgressUrlValidationError("URL host could not be resolved") from exc

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for record in records:
        sockaddr = record[4]
        if not sockaddr:
            continue
        host = str(sockaddr[0]).split("%", maxsplit=1)[0]
        address = _ip_address(host)
        if address is not None:
            addresses.add(address)
    return addresses


def _validate_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    policy: EgressUrlPolicy,
) -> None:
    if policy.allow_private_addresses:
        return
    if not address.is_global or _is_metadata_address(address):
        raise EgressUrlValidationError("URL host resolves to a non-public address")


def _is_metadata_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address == ipaddress.ip_address("169.254.169.254")
