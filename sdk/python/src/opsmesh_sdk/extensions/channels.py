"""Channel adapter contracts for vendor protocols (QQ, DingTalk, ...).

A channel package verifies vendor signatures, normalizes inbound messages and
renders outbound ones. It never owns conversation or task facts: those stay
with the platform owners, and the package only speaks this contract.
"""

from __future__ import annotations

import hashlib
import hmac
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.values import ScopeRef

SUPPORTED_SIGNATURE_ALGORITHMS: Final = ("hmac-sha256",)
MAX_RAW_BODY_BYTES: Final = 1_048_576


class ChannelConnectionStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    ERROR = "error"


class NormalizedMessage(BaseModel):
    """A vendor-neutral inbound message produced by the adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_type: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1, max_length=128)
    conversation_kind: str = Field(min_length=1, max_length=50)
    sender_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)
    content: str = Field(default="", max_length=32_768)
    attachments: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class InboundMessage(BaseModel):
    """A vendor delivery with its signature over the bounded raw bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: str = Field(min_length=1, max_length=50)
    raw_body: bytes = Field(max_length=MAX_RAW_BODY_BYTES)
    signature: str = Field(min_length=1, max_length=512)
    headers: dict[str, str] = Field(default_factory=dict)
    delivered_at_unix: int


class OutboundMessage(BaseModel):
    """A vendor-neutral outbound message the core routes through the adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_type: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=32_768)
    reply_to_message_id: str | None = Field(default=None, max_length=128)
    scope: ScopeRef = Field(default_factory=ScopeRef)


class SignatureVerifier:
    """Verifies a vendor delivery before any normalization happens.

    The signature covers the bounded raw bytes and the delivery timestamp, so
    a captured delivery cannot be replayed outside the window with a valid
    signature. An unsupported algorithm fails closed at construction: a
    channel package needing a vendor-specific scheme extends this contract
    with its server step instead of silently skipping verification.
    """

    def __init__(self, algorithm: str, *, tolerance_seconds: int = 300) -> None:
        if algorithm not in SUPPORTED_SIGNATURE_ALGORITHMS:
            raise ValueError(
                f"unsupported signature algorithm {algorithm!r}; "
                f"supported: {', '.join(SUPPORTED_SIGNATURE_ALGORITHMS)}"
            )
        if tolerance_seconds <= 0:
            raise ValueError("the replay tolerance must be positive")
        self.algorithm = algorithm
        self.tolerance_seconds = tolerance_seconds

    def verify(self, message: InboundMessage, *, secret: bytes, now_unix: int) -> bool:
        """Check authenticity and freshness against the replay window.

        ``secret`` is the vendor shared secret read at the execution boundary;
        it never enters results, logs or event payloads.
        """
        drift = abs(now_unix - message.delivered_at_unix)
        if drift > self.tolerance_seconds:
            return False
        expected = self._signature(message, secret)
        return hmac.compare_digest(expected, message.signature.strip().lower())

    def _signature(self, message: InboundMessage, secret: bytes) -> str:
        mac = hmac.new(secret, digestmod=hashlib.sha256)
        mac.update(str(message.delivered_at_unix).encode())
        mac.update(b".")
        mac.update(message.raw_body)
        return mac.hexdigest()


__all__ = [
    "MAX_RAW_BODY_BYTES",
    "SUPPORTED_SIGNATURE_ALGORITHMS",
    "ChannelConnectionStatus",
    "InboundMessage",
    "NormalizedMessage",
    "OutboundMessage",
    "SignatureVerifier",
]
