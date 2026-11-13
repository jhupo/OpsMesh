"""Bound downloads from explicitly allowed vendor media domains."""

from urllib.parse import urlsplit

import httpx

from .models import AttachmentError, AttachmentSettings


async def download(
    url: str, settings: AttachmentSettings, domains: tuple[str, ...]
) -> bytes:
    """Download HTTPS media without forwarding credentials or redirects."""
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
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream(
                "GET",
                url,
                timeout=settings.download_timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise AttachmentError("Attachment download failed")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result) + len(chunk) > settings.max_file_bytes:
                        raise AttachmentError("Attachment is too large")
                    result.extend(chunk)
                return bytes(result)
    except httpx.HTTPError:
        raise AttachmentError("Attachment download failed") from None
