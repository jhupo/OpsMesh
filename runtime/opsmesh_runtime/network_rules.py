"""Generate nftables transactions from validated identities, never shell text."""

import ipaddress

from .egress_policy import ExecutionNetworkPolicy, execution_port

TABLE = "opsmesh_execution"

# The shared proxy and internet executions cannot reach host services, metadata
# endpoints or other containers. Explicit private-network access requires an
# isolated deployment; it is never inherited from another shared execution.
BLOCKED_V4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/3",
)
BLOCKED_V6 = ("::/128", "::1/128", "::ffff:0:0/96", "fc00::/7", "fe80::/10", "ff00::/8")


def initialize_rules(proxy_uid: int, resolvers: tuple[str, ...]) -> str:
    rules = [
        f"add table inet {TABLE}",
        f"add chain inet {TABLE} output "
        "{ type filter hook output priority filter; policy drop; }",
        f"add chain inet {TABLE} input {{ type filter hook input priority filter; policy drop; }}",
        f'add rule inet {TABLE} input iifname "lo" accept',
        f"add rule inet {TABLE} input ct state established,related accept",
        f"add chain inet {TABLE} public",
        f"add rule inet {TABLE} public ip daddr {{ {', '.join(BLOCKED_V4)} }} drop",
        f"add rule inet {TABLE} public ip6 daddr {{ {', '.join(BLOCKED_V6)} }} drop",
        f"add rule inet {TABLE} public meta l4proto {{ tcp, udp }} accept",
        f"add rule inet {TABLE} output meta skuid {proxy_uid} oifname lo accept",
    ]
    for resolver in resolvers:
        address = ipaddress.ip_address(resolver)
        family = "ip" if address.version == 4 else "ip6"
        rules.append(
            f"add rule inet {TABLE} output meta skuid {proxy_uid} {family} daddr {address} "
            "meta l4proto { tcp, udp } th dport 53 accept"
        )
    rules.append(f"add rule inet {TABLE} output meta skuid {proxy_uid} jump public")
    return "\n".join(rules) + "\n"


def identity_rules(
    policy: ExecutionNetworkPolicy, resolvers: tuple[str, ...], *, exists: bool
) -> str:
    chain = f"execution_{policy.uid}"
    rules = (
        [f"flush chain inet {TABLE} {chain}"]
        if exists
        else [
            f"add chain inet {TABLE} {chain}",
            f"add rule inet {TABLE} output meta skuid {policy.uid} jump {chain}",
        ]
    )
    if policy.mode == "restricted":
        rules.append(
            f"add rule inet {TABLE} {chain} ip daddr 127.0.0.1 "
            f"tcp dport {execution_port(policy.uid)} accept"
        )
    elif policy.mode == "internet":
        for resolver in resolvers:
            address = ipaddress.ip_address(resolver)
            family = "ip" if address.version == 4 else "ip6"
            rules.append(
                f"add rule inet {TABLE} {chain} {family} daddr {address} "
                "meta l4proto { tcp, udp } th dport 53 accept"
            )
        rules.append(f"add rule inet {TABLE} {chain} jump public")
    rules.append(f"add rule inet {TABLE} {chain} drop")
    return "\n".join(rules) + "\n"
