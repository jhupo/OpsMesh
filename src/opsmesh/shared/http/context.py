"""Immutable request evidence, independent of authentication or business policy."""

from dataclasses import dataclass

from starlette.requests import Request

from opsmesh.shared.http.client_ip import resolve_client_ip


@dataclass(frozen=True, slots=True)
class RequestContext:
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str


def get_request_context(request: Request) -> RequestContext:
    app = request.scope.get("app")
    trusted_proxy_hops = getattr(getattr(app, "state", None), "trusted_proxy_hops", 0)
    if not isinstance(trusted_proxy_hops, int) or trusted_proxy_hops < 0:
        trusted_proxy_hops = 0
    trusted_proxy_networks = getattr(getattr(app, "state", None), "trusted_proxy_networks", ())
    return RequestContext(
        source_ip=resolve_client_ip(
            request,
            trusted_proxy_hops=trusted_proxy_hops,
            trusted_proxy_networks=trusted_proxy_networks,
        ),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
        path=request.url.path,
        method=request.method,
    )
