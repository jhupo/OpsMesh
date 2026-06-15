from __future__ import annotations

import re

from backend.app.security.redaction import REDACTED_VALUE, is_sensitive_payload_key

SECRET_LIKE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b(?:ccut|gh[opsu]|github_pat)_[A-Za-z0-9_=-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?"
    ),
)


def redact_context_value(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_payload_key(key_text):
                redacted[key_text] = REDACTED_VALUE
                continue
            redacted[key_text] = redact_context_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_context_value(item) for item in value]
    if isinstance(value, str):
        return redact_secret_like_text(value)
    return value


def redact_secret_like_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for pattern in SECRET_LIKE_PATTERNS:
        redacted = pattern.sub(REDACTED_VALUE, redacted)
    return redacted
