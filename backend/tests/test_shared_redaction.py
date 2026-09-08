import pytest

from backend.app.security.redaction import (
    redact_sensitive_payload,
    redact_sensitive_text,
    redact_text_fragments,
)
from backend.app.webhooks.utils import _safe_headers, _snippet


@pytest.mark.parametrize(
    "secret",
    [
        "sk-testsecret",
        "github_pat_abcdefghijk",
        "ghp_abcdefghijk",
        "AKIA1234567890123456",
        "Bearer abcdefghi",
        "base_url=https://private.test",
    ],
)
def test_all_text_modes_share_detection(secret: str) -> None:
    text = f"before {secret} after"
    assert redact_sensitive_text(text) == "[redacted]"
    assert redact_text_fragments(text) == "before [redacted] after"
    assert redact_sensitive_payload({"items": [{"message": text}]}, text_mode="fragments") == {
        "items": [{"message": "before [redacted] after"}]
    }
    assert _snippet(text) == "[redacted]"


def test_nested_sensitive_keys_and_headers_share_policy() -> None:
    payload = {
        "items": [
            {
                key: "hidden"
                for key in (
                    "password",
                    "api_key",
                    "ciphertext",
                    "encrypted_signing_secret",
                    "X-Auth-Token",
                )
            }
        ],
        "authorization_snapshot_version": 4,
        "count": 2,
    }
    result = redact_sensitive_payload(payload)
    assert set(result["items"][0].values()) == {"[redacted]"}
    assert result["authorization_snapshot_version"] == 4
    assert result["count"] == 2
    assert payload["items"][0]["password"] == "hidden"
    assert _safe_headers({"X-Custom-Secret": "hidden", "Content-Type": "text/plain"}) == {
        "X-Custom-Secret": "[redacted]",
        "Content-Type": "text/plain",
    }
    assert redact_text_fragments(None) is None
