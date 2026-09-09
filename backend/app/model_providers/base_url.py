from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def model_provider_base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return urlparse(base_url).netloc or None


def normalize_openai_compatible_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return base_url
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        normalized_path = path
    elif path == "":
        normalized_path = "/v1"
    else:
        normalized_path = f"{path}/v1"
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))
