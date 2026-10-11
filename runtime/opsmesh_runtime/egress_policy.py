"""Policy consumed by the trusted Runtime supervisor and Squid's native ACL hook.

The execution UID and its loopback proxy port are bound by nftables. Project code
cannot select another policy by changing proxy environment variables or headers.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

FIRST_EXECUTION_UID = 100_000
EXECUTION_SLOTS = 128
FIRST_PROXY_PORT = 20_000


def execution_port(uid: int) -> int:
    if (
        isinstance(uid, bool)
        or not FIRST_EXECUTION_UID <= uid < FIRST_EXECUTION_UID + EXECUTION_SLOTS
    ):
        raise ValueError("Execution UID is outside the node's bounded identity pool")
    return FIRST_PROXY_PORT + uid - FIRST_EXECUTION_UID


@dataclass(frozen=True)
class ExecutionNetworkPolicy:
    allocation_id: UUID
    uid: int
    mode: str
    allowed_domains: tuple[str, ...] = ()
    allowed_cidrs: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = ()
    allowed_protocols: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: dict[str, Any]) -> ExecutionNetworkPolicy:
        if set(value) != {"allocation_id", "uid", "network_policy"}:
            raise ValueError("Execution policy fields are invalid")
        allocation = UUID(value["allocation_id"])
        uid = value["uid"]
        execution_port(uid)
        policy = value["network_policy"]
        if not isinstance(policy, dict):
            raise ValueError("Network policy must be an object")
        if set(policy) - {
            "mode",
            "allowed_domains",
            "allowed_cidrs",
            "allowed_ports",
            "allowed_protocols",
        }:
            raise ValueError("Network policy contains unsupported fields")
        mode = policy.get("mode")
        if mode not in {"none", "restricted", "internet"}:
            raise ValueError("Network mode is invalid")
        domains = policy.get("allowed_domains", [])
        cidrs = policy.get("allowed_cidrs", [])
        ports = policy.get("allowed_ports", [])
        protocols = policy.get("allowed_protocols", [])
        for items in (domains, cidrs, ports, protocols):
            if not isinstance(items, list) or len(items) > 128:
                raise ValueError("Network allowlists must be bounded lists")
        for domain in domains:
            if not isinstance(domain, str) or not domain or len(domain) > 253:
                raise ValueError("Network domain is invalid")
            hostname = domain.removeprefix("*.")
            if any(
                not label or not label.replace("-", "").isalnum() for label in hostname.split(".")
            ):
                raise ValueError("Network domain is invalid")
        for cidr in cidrs:
            ipaddress.ip_network(cidr, strict=True)
        if any(
            isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
            for port in ports
        ):
            raise ValueError("Network port is invalid")
        if protocols and protocols != ["tcp"]:
            raise ValueError("Restricted execution supports TCP through the managed proxy")
        if mode == "restricted" and (not (domains or cidrs) or not ports):
            raise ValueError("Restricted execution requires destinations and ports")
        return cls(
            allocation, uid, mode, tuple(domains), tuple(cidrs), tuple(ports), tuple(protocols)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocation_id": str(self.allocation_id),
            "uid": self.uid,
            "network_policy": {
                "mode": self.mode,
                "allowed_domains": list(self.allowed_domains),
                "allowed_cidrs": list(self.allowed_cidrs),
                "allowed_ports": list(self.allowed_ports),
                "allowed_protocols": list(self.allowed_protocols),
            },
        }

    def permits_proxy(self, host: str, port: int) -> bool:
        if self.mode != "restricted" or port not in self.allowed_ports:
            return False
        host = host.lower().rstrip(".")
        if any(
            host == domain or (domain.startswith("*.") and host.endswith(domain[1:]))
            for domain in self.allowed_domains
        ):
            return True
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return False
        return any(address in ipaddress.ip_network(cidr) for cidr in self.allowed_cidrs)
