"""Bounded outbound HTTP with egress-validated, connection-pinned targets."""

import asyncio

import httpx2 as httpx

from opsmesh.shared.security.url_validation import (
    EgressURLPolicy,
    resolve_egress_address,
    validate_url_shape,
)

Origin = tuple[str, str, int]


def _origin_of(url: httpx.URL) -> Origin:
    return url.scheme, url.host, url.port or (443 if url.scheme == "https" else 80)


def _validated_origin(base_url: str, policy: EgressURLPolicy) -> Origin:
    try:
        validate_url_shape(base_url, allowed_schemes=policy.allowed_schemes)
        return _origin_of(httpx.URL(base_url))
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Outbound HTTP origin is invalid") from None


async def _resolve_address(url: httpx.URL, origin: Origin, policy: EgressURLPolicy) -> str:
    if _origin_of(url) != origin:
        raise ValueError("Cross-origin HTTP requests are not allowed")
    try:
        return await asyncio.to_thread(resolve_egress_address, str(url), policy=policy)
    except (ValueError, OSError):
        raise ValueError("Outbound HTTP address was rejected") from None


def _pinned_request(request: httpx.Request, address: str) -> httpx.Request:
    headers = request.headers.copy()
    headers["Host"] = request.url.netloc.decode("ascii")
    extensions = {**request.extensions, "sni_hostname": request.url.host}
    extensions.pop("target", None)
    return type(request)(
        request.method,
        request.url.copy_with(host=address),
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Connect to validated addresses while retaining the configured HTTPS origin."""

    def __init__(
        self,
        base_url: str,
        *,
        policy: EgressURLPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy or EgressURLPolicy(frozenset({"https"}), allow_unresolved_hosts=False)
        self._origin = _validated_origin(base_url, self._policy)
        self._transport = transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            address = await _resolve_address(request.url, self._origin, self._policy)
        except ValueError as exc:
            raise httpx.ConnectError(str(exc), request=request) from None
        try:
            return await self._transport.handle_async_request(_pinned_request(request, address))
        except httpx.TransportError:
            raise httpx.TransportError("Outbound HTTP request failed", request=request) from None

    async def aclose(self) -> None:
        await self._transport.aclose()
