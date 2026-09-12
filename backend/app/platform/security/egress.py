from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


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
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise EgressUrlValidationError("URL scheme is not allowed")
    if not parsed.hostname:
        raise EgressUrlValidationError("URL host is required")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EgressUrlValidationError("URL port is invalid") from exc

    host = _normalized_hostname(parsed.hostname)
    if _is_blocked_hostname(host):
        raise EgressUrlValidationError("URL host is not allowed")

    literal_address = _ip_address(host)
    if literal_address is not None:
        _validate_address(literal_address, policy=policy)
        return url

    for address in _resolve_host(host, policy=policy):
        _validate_address(address, policy=policy)
    return url


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
