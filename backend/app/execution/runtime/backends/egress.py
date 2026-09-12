from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

EgressMode = Literal["none", "restricted", "internet"]


@dataclass(frozen=True, slots=True)
class RuntimeEgressPolicy:
    mode: EgressMode
    allowed_domains: tuple[str, ...] = ()
    allowed_cidrs: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = ()
    allowed_protocols: tuple[str, ...] = ()
    gateway_network: str | None = None
    proxy_url: str | None = None

    @property
    def disabled(self) -> bool:
        return self.mode == "none"

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "disabled": self.disabled,
            "allowed_domains": list(self.allowed_domains),
            "allowed_cidrs": list(self.allowed_cidrs),
            "allowed_ports": list(self.allowed_ports),
            "allowed_protocols": list(self.allowed_protocols),
            "gateway_network": self.gateway_network,
            "proxy_url": self.proxy_url,
            "enforcement": "docker_network_gateway"
            if self.mode == "restricted"
            else "docker_network_none"
            if self.mode == "none"
            else "docker_bridge",
        }


class RuntimeEgressPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_egress_policy(
    raw: dict[str, object] | None,
    *,
    forced_disabled: bool,
) -> RuntimeEgressPolicy:
    """Normalize an effective network policy and fail closed for invalid restrictions."""
    policy = raw or {}
    if forced_disabled or _is_disabled(policy):
        return RuntimeEgressPolicy(mode="none")
    mode = _mode(policy)
    if mode == "none":
        return RuntimeEgressPolicy(mode="none")
    if mode == "internet":
        return RuntimeEgressPolicy(mode="internet")

    gateway_network = _string(policy.get("gateway_network"))
    if gateway_network is None:
        raise RuntimeEgressPolicyError(
            "runtime_egress_gateway_required",
            "Restricted runtime egress requires a managed Docker gateway network",
        )
    return RuntimeEgressPolicy(
        mode="restricted",
        allowed_domains=_domains(policy.get("allowed_domains")),
        allowed_cidrs=_cidrs(policy.get("allowed_cidrs")),
        allowed_ports=_ports(policy.get("allowed_ports")),
        allowed_protocols=_protocols(policy.get("allowed_protocols")),
        gateway_network=gateway_network,
        proxy_url=_proxy_url(policy.get("proxy_url")),
    )


def _mode(policy: dict[str, object]) -> EgressMode:
    raw_mode = policy.get("mode") or policy.get("egress_mode")
    if isinstance(raw_mode, str):
        normalized = raw_mode.strip().lower().replace("-", "_")
        if normalized in {"none", "disabled", "off", "deny"}:
            return "none"
        if normalized in {"restricted", "allowlist", "allow_list", "gateway"}:
            return "restricted"
        if normalized in {"internet", "online", "public", "bridge"}:
            return "internet"
        raise RuntimeEgressPolicyError(
            "runtime_egress_mode_invalid",
            "Runtime egress mode is unsupported",
        )
    return "internet" if policy.get("allow_network") is True else "none"


def _is_disabled(policy: dict[str, object]) -> bool:
    return policy.get("disabled") is True or policy.get("allow_network") is False


def _domains(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeEgressPolicyError(
            "runtime_egress_domains_invalid",
            "Runtime egress allowed_domains must be a list",
        )
    domains: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RuntimeEgressPolicyError(
                "runtime_egress_domains_invalid",
                "Runtime egress domains must be strings",
            )
        domain = item.strip().lower().rstrip(".")
        if not domain or len(domain) > 253 or "://" in domain or "/" in domain:
            raise RuntimeEgressPolicyError(
                "runtime_egress_domain_invalid",
                "Runtime egress domain is invalid",
            )
        if any(not label or len(label) > 63 for label in domain.split(".")):
            raise RuntimeEgressPolicyError(
                "runtime_egress_domain_invalid",
                "Runtime egress domain is invalid",
            )
        domains.append(domain)
    return tuple(dict.fromkeys(domains))


def _cidrs(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeEgressPolicyError(
            "runtime_egress_cidrs_invalid",
            "Runtime egress allowed_cidrs must be a list",
        )
    cidrs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RuntimeEgressPolicyError(
                "runtime_egress_cidr_invalid",
                "Runtime egress CIDRs must be strings",
            )
        try:
            cidr = str(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError as exc:
            raise RuntimeEgressPolicyError(
                "runtime_egress_cidr_invalid",
                "Runtime egress CIDR is invalid",
            ) from exc
        cidrs.append(cidr)
    return tuple(dict.fromkeys(cidrs))


def _ports(value: object) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeEgressPolicyError(
            "runtime_egress_ports_invalid",
            "Runtime egress allowed_ports must be a list",
        )
    ports: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 65_535:
            raise RuntimeEgressPolicyError(
                "runtime_egress_port_invalid",
                "Runtime egress port must be between 1 and 65535",
            )
        ports.append(item)
    return tuple(dict.fromkeys(ports))


def _protocols(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeEgressPolicyError(
            "runtime_egress_protocols_invalid",
            "Runtime egress allowed_protocols must be a list",
        )
    protocols: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.strip().lower() not in {"tcp", "udp"}:
            raise RuntimeEgressPolicyError(
                "runtime_egress_protocol_invalid",
                "Runtime egress protocol must be tcp or udp",
            )
        protocols.append(item.strip().lower())
    return tuple(dict.fromkeys(protocols))


def _proxy_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeEgressPolicyError(
            "runtime_egress_proxy_invalid",
            "Runtime egress proxy URL must be a string",
        )
    proxy = value.strip()
    parsed = urlsplit(proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise RuntimeEgressPolicyError(
            "runtime_egress_proxy_invalid",
            "Runtime egress proxy URL must be an unauthenticated HTTP(S) endpoint",
        )
    return proxy


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
