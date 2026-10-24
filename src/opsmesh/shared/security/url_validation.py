"""Validate outbound URLs and resolve egress-safe connection addresses."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

from opsmesh.shared.security.redaction import RedactionRules, is_sensitive_payload_key


@dataclass(frozen=True)
class EgressURLPolicy:
    allowed_schemes: frozenset[str]
    allow_unresolved_hosts: bool = True
    allow_private_addresses: bool = False
    query_redaction_rules: RedactionRules = RedactionRules()


class EgressURLValidationError(ValueError):
    pass


def validate_egress_url(url: str, *, policy: EgressURLPolicy) -> str:
    validate_url_shape(
        url,
        allowed_schemes=policy.allowed_schemes,
        query_redaction_rules=policy.query_redaction_rules,
    )
    parsed = urlparse(url)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EgressURLValidationError("URL port is invalid") from exc

    hostname = parsed.hostname
    if hostname is None:
        raise EgressURLValidationError("URL host is required")
    host = _normalized_hostname(hostname)
    if not policy.allow_private_addresses and _is_blocked_hostname(host):
        raise EgressURLValidationError("URL host is not allowed")

    literal_address = _ip_address(host)
    if literal_address is not None:
        _validate_address(literal_address, policy=policy)
        return url

    for address in _resolve_host(host, policy=policy):
        _validate_address(address, policy=policy)
    return url


def validate_url_shape(
    url: str,
    *,
    allowed_schemes: frozenset[str],
    query_redaction_rules: RedactionRules | None = None,
) -> str:
    """Validate URL syntax; query restrictions come only from caller-supplied rules."""
    if not isinstance(url, str) or not url.strip():
        raise EgressURLValidationError("URL is required")
    if any(ord(character) < 0x20 for character in url):
        raise EgressURLValidationError("URL contains control characters")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in allowed_schemes:
        raise EgressURLValidationError("URL scheme is not allowed")
    if not parsed.hostname:
        raise EgressURLValidationError("URL host is required")
    if parsed.username is not None or parsed.password is not None:
        raise EgressURLValidationError("URL credentials are not allowed")
    if parsed.fragment:
        raise EgressURLValidationError("URL fragments are not allowed")
    if query_redaction_rules is not None:
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if is_sensitive_payload_key(key, rules=query_redaction_rules):
                raise EgressURLValidationError("URL query must not contain credentials")
    return url


def resolve_egress_address(url: str, *, policy: EgressURLPolicy) -> str:
    """Resolve once and return a validated IP for the transport to connect to.

    Configuration may allow unresolved hosts; an actual connection never does.
    Every returned DNS address must satisfy the policy before one is selected.
    """
    validate_url_shape(
        url,
        allowed_schemes=policy.allowed_schemes,
        query_redaction_rules=policy.query_redaction_rules,
    )
    parsed = urlparse(url)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EgressURLValidationError("URL port is invalid") from exc
    host = _normalized_hostname(parsed.hostname or "")
    if not policy.allow_private_addresses and _is_blocked_hostname(host):
        raise EgressURLValidationError("URL host is not allowed")
    literal = _ip_address(host)
    addresses = (
        {literal}
        if literal is not None
        else _resolve_host(
            host,
            policy=EgressURLPolicy(
                allowed_schemes=policy.allowed_schemes,
                allow_unresolved_hosts=False,
                allow_private_addresses=policy.allow_private_addresses,
                query_redaction_rules=policy.query_redaction_rules,
            ),
        )
    )
    if not addresses:
        raise EgressURLValidationError("URL host could not be resolved")
    for address in addresses:
        _validate_address(address, policy=policy)
    return str(sorted(addresses, key=lambda address: (address.version, int(address)))[0])


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
    policy: EgressURLPolicy,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if policy.allow_unresolved_hosts:
            return set()
        raise EgressURLValidationError("URL host could not be resolved") from exc

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
    policy: EgressURLPolicy,
) -> None:
    if policy.allow_private_addresses:
        return
    if not address.is_global:
        raise EgressURLValidationError("URL host resolves to a non-public address")
