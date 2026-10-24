"""Resolve the client source IP only across a configured, trusted proxy chain."""

from collections.abc import Sequence
from ipaddress import ip_address, ip_network

from starlette.requests import Request


def resolve_client_ip(
    request: Request,
    *,
    trusted_proxy_hops: int = 0,
    trusted_proxy_networks: Sequence[str] = (),
) -> str | None:
    """Accept forwarded addresses only across the configured, trusted proxy chain."""
    peer = request.client.host if request.client is not None else None
    if trusted_proxy_hops <= 0 or peer is None or not trusted_proxy_networks:
        return peer
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return peer
    try:
        networks = [ip_network(network) for network in trusted_proxy_networks]
        peer_address = ip_address(peer)
        if not any(peer_address in network for network in networks):
            return peer
        chain = [ip_address(item.strip()) for item in forwarded_for.split(",")]
        if len(chain) < trusted_proxy_hops:
            return peer
        if any(
            not any(address in network for network in networks)
            for address in chain[len(chain) - trusted_proxy_hops + 1 :]
        ):
            return peer
        return str(chain[-trusted_proxy_hops])
    except ValueError:
        return peer
