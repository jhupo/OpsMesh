"""HMAC-SHA256 verification for DingTalk robot callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac


def _expected(secret: str, timestamp: str) -> str:
    message = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(secret: str, timestamp: str, signature: str) -> bool:
    """Check a DingTalk timestamp/signature header pair."""
    return hmac.compare_digest(_expected(secret, timestamp), signature)
