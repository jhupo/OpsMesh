from ipaddress import ip_address

from starlette.requests import Request

from backend.app.observability.audit.security_events import SecurityRequestContext


def resolve_client_ip(request: Request, *, trusted_proxy_hops: int = 0) -> str | None:
    peer = request.client.host if request.client is not None else None
    if trusted_proxy_hops <= 0:
        return peer
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return peer
    chain = [item.strip() for item in forwarded_for.split(",") if item.strip()]
    if len(chain) < trusted_proxy_hops:
        return peer
    candidate = chain[-trusted_proxy_hops]
    try:
        return str(ip_address(candidate))
    except ValueError:
        return peer


def security_request_context(request: Request) -> SecurityRequestContext:
    app = request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    trusted_proxy_hops = getattr(settings, "trusted_proxy_hops", 0)
    if not isinstance(trusted_proxy_hops, int) or trusted_proxy_hops < 0:
        trusted_proxy_hops = 0
    return SecurityRequestContext(
        source_ip=resolve_client_ip(
            request,
            trusted_proxy_hops=trusted_proxy_hops,
        ),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
        path=request.url.path,
        method=request.method,
    )
