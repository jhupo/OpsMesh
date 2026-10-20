"""Platform protection rules, explicitly supplied by application assembly."""

import re

from opsmesh.shared.security.redaction import RedactionRules

_EXACT_KEYS = {
    "base_url",
    "container_id",
    "ciphertext",
    "credential",
    "credentials",
    "docker_container_id",
    "endpoint_url",
    "external_ref",
    "remote_url",
    "storage_key",
    "session_id",
    "invitation_code",
}
_KEY_PARTS = {
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "password",
    "secret",
    "token",
}
_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:ccut|gh[opsu]|github_pat)_[A-Za-z0-9_=-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|authorization|password|(?:client[_ -]?)?secret|"
        r"(?:(?:access|refresh)[_ -]?)?token|base[_ -]?url|"
        r"(?:x[_ -]?)?session[_ -]?id|invitation[_ -]?code)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?",
        re.IGNORECASE,
    ),
)

PLATFORM_REDACTION = RedactionRules(
    exact_keys=frozenset(_EXACT_KEYS),
    key_parts=frozenset(_KEY_PARTS),
    text_patterns=_TEXT_PATTERNS,
)
