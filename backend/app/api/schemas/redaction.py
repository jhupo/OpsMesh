SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "cookie",
    "endpoint_url",
    "headers",
    "password",
    "remote_url",
    "secret",
    "token",
}


def redact_sensitive_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        key_text = str(key)
        if is_sensitive_payload_key(key_text):
            redacted[key_text] = "[redacted]"
            continue
        if isinstance(value, dict):
            redacted[key_text] = redact_sensitive_payload(value)
            continue
        if isinstance(value, list):
            redacted[key_text] = [redact_sensitive_payload_item(item) for item in value]
            continue
        redacted[key_text] = value
    return redacted


def redact_sensitive_payload_item(value: object) -> object:
    if isinstance(value, dict):
        return redact_sensitive_payload(value)
    return value


def is_sensitive_payload_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(sensitive in normalized for sensitive in SENSITIVE_PAYLOAD_KEYS)
