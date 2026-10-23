"""S2 security primitive tests: authenticated encryption and redaction."""

from __future__ import annotations

import re

import pytest

from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import (
    REDACTED_VALUE,
    RedactionRules,
    is_sensitive_payload_key,
    is_sensitive_payload_value,
    redact_sensitive_payload,
    redact_sensitive_text,
    redact_text_fragments,
)

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="
ROTATED_KEY = "Kk7BPplGjc33Pq4FTT7HPYmkukUWuV8EuxFVTMwAEmA="


def cipher() -> SecretCipher:
    return SecretCipher(key=TEST_KEY, key_id="primary")


def test_encrypt_decrypt_round_trip() -> None:
    encrypted = cipher().encrypt_payload({"workflow_id": "run-1", "attempt": 2})
    decrypted = cipher().decrypt_payload(encrypted.ciphertext, key_id=encrypted.key_id)
    assert decrypted == {"workflow_id": "run-1", "attempt": 2}
    assert encrypted.fingerprint.startswith("sha256:")


def test_decrypt_rejects_foreign_ciphertext() -> None:
    with pytest.raises(ValueError):
        cipher().decrypt_payload("not-a-ciphertext", key_id="primary")


def test_decrypt_rejects_unknown_key_id() -> None:
    encrypted = cipher().encrypt_payload({"a": 1})
    with pytest.raises(ValueError):
        cipher().decrypt_payload(encrypted.ciphertext, key_id="rotated")


def test_previous_keys_decrypt_old_payloads() -> None:
    rotated = SecretCipher(key=ROTATED_KEY, key_id="rotated", previous_keys={"primary": TEST_KEY})
    sealed = cipher().encrypt_payload({"a": 1})
    assert rotated.decrypt_payload(sealed.ciphertext) == {"a": 1}


def test_duplicate_key_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        SecretCipher(key=TEST_KEY, key_id="primary", previous_keys={"primary": ROTATED_KEY})


def test_sensitive_keys_are_redacted_recursively() -> None:
    redacted = redact_sensitive_payload(
        {
            "api_key": "sk-live-123",
            "nested": {"password": "hunter2", "safe": "value"},
            "items": [{"token": "abc"}],
        },
        rules=PLATFORM_REDACTION,
    )
    assert redacted["api_key"] == REDACTED_VALUE
    assert redacted["nested"] == {"password": REDACTED_VALUE, "safe": "value"}
    assert redacted["items"] == [{"token": REDACTED_VALUE}]


@pytest.mark.parametrize(
    "key",
    ["apiKey", "client_secret", "authorization", "session_id", "storage_key"],
)
def test_sensitive_key_detection(key: str) -> None:
    assert is_sensitive_payload_key(key, rules=PLATFORM_REDACTION)


@pytest.mark.parametrize("key", ["workflow_id", "attempt", "display_name"])
def test_plain_keys_are_not_sensitive(key: str) -> None:
    assert not is_sensitive_payload_key(key, rules=PLATFORM_REDACTION)


@pytest.mark.parametrize(
    "value",
    ["sk-abcdef123456", "Bearer abcdef123456", "AKIAABCDEFGHIJKLMNOP"],
)
def test_sensitive_values_are_detected(value: str) -> None:
    assert is_sensitive_payload_value(value, rules=PLATFORM_REDACTION)
    assert redact_sensitive_text(value, rules=PLATFORM_REDACTION) == REDACTED_VALUE


def test_plain_values_are_preserved() -> None:
    assert redact_sensitive_text("run-123", rules=PLATFORM_REDACTION) == "run-123"


def test_redaction_uses_only_supplied_rules_and_does_not_mutate_input() -> None:
    payload = {"password": "visible", "caseNumber": "C42", "nested": [{"text": "C42"}]}
    rules = RedactionRules(
        exact_keys=frozenset({"case_number"}),
        text_patterns=(re.compile(r"C[0-9]+"),),
    )
    assert redact_sensitive_payload(payload, rules=rules) == {
        "password": "visible",
        "caseNumber": REDACTED_VALUE,
        "nested": [{"text": REDACTED_VALUE}],
    }
    assert redact_sensitive_payload(payload, rules=RedactionRules()) == payload
    assert payload["caseNumber"] == "C42"
    assert redact_text_fragments("case C42 closed", rules=rules) == "case [redacted] closed"


def test_key_parts_are_supplied_by_caller_and_match_complete_segments() -> None:
    rules = RedactionRules(key_parts=frozenset({"private_code"}))
    assert is_sensitive_payload_key("customerPrivateCodeValue", rules=rules)
    assert not is_sensitive_payload_key("customerPrivateCodes", rules=rules)
    assert not is_sensitive_payload_key("api_key", rules=rules)
