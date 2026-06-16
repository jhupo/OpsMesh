from urllib.parse import urlparse

from backend.app.api.schemas.redaction import is_sensitive_payload_key


def redacted_connection(connection: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in connection.items():
        key_text = str(key)
        if is_sensitive_payload_key(key_text):
            redacted[key_text] = "[redacted]"
            continue
        if key_text in {"url", "endpoint"} and isinstance(value, str):
            redacted[f"{key_text}_configured"] = bool(value)
            redacted[f"{key_text}_host"] = _url_host(value)
            continue
        if isinstance(value, dict):
            redacted[key_text] = redacted_connection(value)
            continue
        if isinstance(value, list):
            redacted[key_text] = [_redacted_connection_item(item) for item in value]
            continue
        redacted[key_text] = value
    return redacted


def _redacted_connection_item(value: object) -> object:
    if isinstance(value, dict):
        return redacted_connection(value)
    return value


def _url_host(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc or None
