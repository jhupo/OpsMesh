import re
from typing import Literal, overload

_SENSITIVE_EXACT_KEYS = {
    "base_url",
    "container_id",
    "ciphertext",
    "credential",
    "credentials",
    "docker_container_id",
    "endpoint_url",
    "external_ref",
    "remote_url",
}
_SAFE_EXACT_KEYS = {
    "authorization_snapshot_version",
}
_SENSITIVE_KEY_PARTS = {
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "password",
    "secret",
    "token",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:ccut|gh[opsu]|github_pat)_[A-Za-z0-9_=-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|apikey|authorization|password|secret|token|base[_ -]?url)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?",
        re.IGNORECASE,
    ),
)

REDACTED_VALUE = "[redacted]"


def redact_sensitive_payload(
    payload: dict[str, object], *, text_mode: Literal["whole", "fragments"] = "whole"
) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        key_text = str(key)
        if is_sensitive_payload_key(key_text):
            redacted[key_text] = REDACTED_VALUE
            continue
        redacted[key_text] = redact_sensitive_payload_item(value, text_mode=text_mode)
    return redacted


def redact_sensitive_payload_item(
    value: object, *, text_mode: Literal["whole", "fragments"] = "whole"
) -> object:
    if isinstance(value, dict):
        return redact_sensitive_payload(value, text_mode=text_mode)
    if isinstance(value, list):
        return [redact_sensitive_payload_item(item, text_mode=text_mode) for item in value]
    if isinstance(value, str):
        return (
            redact_text_fragments(value)
            if text_mode == "fragments"
            else redact_sensitive_text(value)
        )
    return value


def is_sensitive_payload_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SAFE_EXACT_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized in _SENSITIVE_KEY_PARTS:
        return True
    if normalized.endswith("_api_key"):
        return True
    return any(part in _SENSITIVE_KEY_PARTS for part in normalized.split("_"))


def is_sensitive_payload_value(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SENSITIVE_VALUE_PATTERNS)


def redact_sensitive_text(value: str) -> str:
    return REDACTED_VALUE if is_sensitive_payload_value(value) else value


@overload
def redact_text_fragments(value: str) -> str: ...


@overload
def redact_text_fragments(value: None) -> None: ...


def redact_text_fragments(value: str | None) -> str | None:
    if value is None:
        return None
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        value = pattern.sub(REDACTED_VALUE, value)
    return value
