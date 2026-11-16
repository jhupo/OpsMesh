"""Bound downloads from explicitly allowed vendor media domains."""

from urllib.parse import urljoin, urlsplit

import anyio
import httpx

from .models import AttachmentError, AttachmentSettings

_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _validate_origin(url: str, domains: tuple[str, ...]) -> None:
    """Validate every hop before contacting a vendor media host."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise AttachmentError(
            "Attachment download origin is not allowed"
        ) from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not any(
            host == domain or host.endswith("." + domain) for domain in domains
        )
    ):
        raise AttachmentError("Attachment download origin is not allowed")


async def download(
    url: str, settings: AttachmentSettings, domains: tuple[str, ...]
) -> bytes:
    """Follow bounded vendor redirects without forwarding credentials."""
    try:
        with anyio.fail_after(settings.download_timeout):
            async with httpx.AsyncClient(trust_env=False) as client:
                for hop in range(_MAX_REDIRECTS + 1):
                    _validate_origin(url, domains)
                    client.cookies.clear()
                    async with client.stream(
                        "GET",
                        url,
                        timeout=settings.download_timeout,
                        follow_redirects=False,
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location or hop == _MAX_REDIRECTS:
                                raise AttachmentError(
                                    "Attachment download redirect limit "
                                    "or missing location"
                                )
                            url = urljoin(url, location)
                            continue
                        if response.status_code != 200:
                            raise AttachmentError(
                                "Attachment download failed "
                                f"(HTTP {response.status_code})"
                            )
                        return await _read_content(response, settings)
    except (TimeoutError, httpx.TimeoutException):
        raise AttachmentError("Attachment download timed out") from None
    except httpx.HTTPError as error:
        raise AttachmentError(
            f"Attachment download failed ({type(error).__name__})"
        ) from None
    raise AttachmentError("Attachment download redirect limit exceeded")


async def _read_content(
    response: httpx.Response, settings: AttachmentSettings
) -> bytes:
    """Bound decoded bytes even when Content-Length is absent or compressed."""
    result = bytearray()
    async for chunk in response.aiter_bytes():
        if len(result) + len(chunk) > settings.max_file_bytes:
            raise AttachmentError("Attachment is too large")
        result.extend(chunk)
    return bytes(result)
