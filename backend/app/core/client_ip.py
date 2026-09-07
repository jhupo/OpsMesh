from ipaddress import ip_address

from starlette.requests import Request


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
