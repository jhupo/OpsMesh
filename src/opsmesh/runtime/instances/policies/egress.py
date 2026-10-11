from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Literal, cast

EgressMode = Literal["none", "restricted", "internet"]


@dataclass(frozen=True, slots=True)
class RuntimeEgressPolicy:
    mode: EgressMode
    allowed_domains: tuple[str, ...] = ()
    allowed_cidrs: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = ()
    allowed_protocols: tuple[str, ...] = ()

    @property
    def disabled(self) -> bool:
        return self.mode == "none"

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "allowed_domains": list(self.allowed_domains),
            "allowed_cidrs": list(self.allowed_cidrs),
            "allowed_ports": list(self.allowed_ports),
            "allowed_protocols": list(self.allowed_protocols),
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
    allowed = {
        "mode",
        "allowed_domains",
        "allowed_cidrs",
        "allowed_ports",
        "allowed_protocols",
    }
    if set(policy) - allowed:
        raise RuntimeEgressPolicyError(
            "runtime_egress_field_invalid", "Unknown runtime egress field"
        )
    mode = _mode(policy)
    if forced_disabled:
        return RuntimeEgressPolicy(mode="none")
    if mode == "none":
        return RuntimeEgressPolicy(mode="none")
    if mode == "internet":
        return RuntimeEgressPolicy(mode="internet")

    domains = _domains(policy.get("allowed_domains"))
    cidrs = _cidrs(policy.get("allowed_cidrs"))
    ports = _ports(policy.get("allowed_ports"))
    protocols = _protocols(policy.get("allowed_protocols"))
    if not (domains or cidrs) or not ports:
        raise RuntimeEgressPolicyError(
            "runtime_egress_destinations_required",
            "Restricted Runtime execution requires destinations and ports",
        )
    if protocols and protocols != ("tcp",):
        raise RuntimeEgressPolicyError(
            "runtime_egress_protocol_invalid",
            "Restricted Runtime execution supports TCP through the managed proxy",
        )
    return RuntimeEgressPolicy(
        mode="restricted",
        allowed_domains=domains,
        allowed_cidrs=cidrs,
        allowed_ports=ports,
        allowed_protocols=protocols,
    )


def _mode(policy: dict[str, object]) -> EgressMode:
    mode = policy.get("mode", "none")
    if mode not in {"none", "restricted", "internet"}:
        raise RuntimeEgressPolicyError(
            "runtime_egress_mode_invalid", "Runtime egress mode is unsupported"
        )
    return cast(EgressMode, mode)


def _domains(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 128:
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
        if any(
            not label or len(label) > 63 or not label.replace("-", "").isalnum()
            for label in domain.removeprefix("*.").split(".")
        ):
            raise RuntimeEgressPolicyError(
                "runtime_egress_domain_invalid",
                "Runtime egress domain is invalid",
            )
        domains.append(domain)
    return tuple(dict.fromkeys(domains))


def _cidrs(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 128:
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
    if not isinstance(value, list) or len(value) > 128:
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
    if not isinstance(value, list) or len(value) > 128:
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
