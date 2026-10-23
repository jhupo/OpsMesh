"""Recursive redaction using only the rules explicitly supplied by the caller."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, overload


@dataclass(frozen=True)
class RedactionRules:
    """Caller-owned field names and text patterns; an empty ruleset matches nothing."""

    exact_keys: frozenset[str] = frozenset()
    key_parts: frozenset[str] = frozenset()
    text_patterns: tuple[re.Pattern[str], ...] = ()


REDACTED_VALUE = "[redacted]"


def require_redaction_rules(value: object) -> RedactionRules:
    """Validate caller-supplied rules without silently using an empty ruleset."""
    if not isinstance(value, RedactionRules):
        raise RuntimeError("Redaction rules are not assembled")
    return value


def redact_sensitive_payload(
    payload: dict[str, object],
    *,
    rules: RedactionRules,
    text_mode: Literal["whole", "fragments"] = "whole",
) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        key_text = str(key)
        if is_sensitive_payload_key(key_text, rules=rules):
            redacted[key_text] = REDACTED_VALUE
            continue
        redacted[key_text] = redact_sensitive_payload_item(value, rules=rules, text_mode=text_mode)
    return redacted


def redact_sensitive_payload_item(
    value: object,
    *,
    rules: RedactionRules,
    text_mode: Literal["whole", "fragments"] = "whole",
) -> object:
    if isinstance(value, dict):
        return redact_sensitive_payload(value, rules=rules, text_mode=text_mode)
    if isinstance(value, list | tuple):
        return [
            redact_sensitive_payload_item(item, rules=rules, text_mode=text_mode) for item in value
        ]
    if isinstance(value, str):
        return (
            redact_text_fragments(value, rules=rules)
            if text_mode == "fragments"
            else redact_sensitive_text(value, rules=rules)
        )
    return value


def is_sensitive_payload_key(key: str, *, rules: RedactionRules) -> bool:
    # Split both camelCase and acronym boundaries before matching key parts.
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    if normalized in rules.exact_keys:
        return True
    if normalized in rules.key_parts:
        return True
    return any(f"_{part}_" in f"_{normalized}_" for part in rules.key_parts)


def is_sensitive_payload_value(value: str, *, rules: RedactionRules) -> bool:
    return any(pattern.search(value) is not None for pattern in rules.text_patterns)


def redact_sensitive_text(value: str, *, rules: RedactionRules) -> str:
    return REDACTED_VALUE if is_sensitive_payload_value(value, rules=rules) else value


@overload
def redact_text_fragments(value: str, *, rules: RedactionRules) -> str: ...


@overload
def redact_text_fragments(value: None, *, rules: RedactionRules) -> None: ...


def redact_text_fragments(value: str | None, *, rules: RedactionRules) -> str | None:
    if value is None:
        return None
    for pattern in rules.text_patterns:
        value = pattern.sub(REDACTED_VALUE, value)
    return value
