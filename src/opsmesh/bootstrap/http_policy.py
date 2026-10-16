"""Application gateway policy: path classification and client identification."""

from collections.abc import Callable
from hashlib import sha256

from starlette.requests import Request

from opsmesh.config import Settings
from opsmesh.shared.http.client_ip import resolve_client_ip
from opsmesh.shared.http.middleware import GatewayRateLimitPolicy

AUTHENTICATION_PATH_SEGMENT = "auth"
PLATFORM_ADMIN_PATH_SEGMENT = "platform"


def gateway_policy(settings: Settings) -> Callable[[str], GatewayRateLimitPolicy]:
    """Classify request paths into gateway rate-limit policies.

    Feature routers register below the configured API prefix; the first path
    segment below that prefix selects the policy class. Authentication surfaces
    and platform administration fail closed when the limiter backend is
    unavailable; the general API policy stays available.
    """
    prefix_segments = [segment for segment in settings.api_prefix.split("/") if segment]
    authentication = GatewayRateLimitPolicy(
        "authentication", settings.auth_rate_limit_requests, fail_closed=True
    )
    platform = GatewayRateLimitPolicy(
        "platform", settings.admin_rate_limit_requests, fail_closed=True
    )
    api = GatewayRateLimitPolicy("api", settings.api_rate_limit_requests, fail_closed=False)

    def policy_for_path(path: str) -> GatewayRateLimitPolicy:
        segments = [segment for segment in path.split("/") if segment]
        if segments[: len(prefix_segments)] != prefix_segments:
            return api
        scoped = segments[len(prefix_segments) :]
        if not scoped:
            return api
        if scoped[0] == AUTHENTICATION_PATH_SEGMENT:
            return authentication
        if scoped[0] == PLATFORM_ADMIN_PATH_SEGMENT:
            return platform
        return api

    return policy_for_path


def gateway_identifier(
    request: Request, policy: GatewayRateLimitPolicy, *, settings: Settings
) -> str:
    # Unauthenticated session headers cannot be used to reset gateway limits.
    client_ip = resolve_client_ip(
        request,
        trusted_proxy_hops=settings.trusted_proxy_hops,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    return sha256(f"{policy.name}|{client_ip or 'unknown'}".encode()).hexdigest()
