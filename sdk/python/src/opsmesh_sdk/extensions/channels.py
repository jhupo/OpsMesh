"""Channel adapter contracts for vendor protocols (QQ, DingTalk, ...).

A channel package verifies vendor signatures, normalizes inbound messages and
renders outbound ones. It never owns conversation or task facts: those stay
with the platform owners, and the package only speaks this contract.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.values import ScopeRef


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
    """A verified vendor delivery with its replay-window proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: str = Field(min_length=1, max_length=50)
    raw_body: bytes
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
    """Verifies a vendor delivery before any normalization happens."""

    def __init__(self, algorithm: str, *, tolerance_seconds: int = 300) -> None:
        if tolerance_seconds <= 0:
            raise ValueError("the replay tolerance must be positive")
        self.algorithm = algorithm
        self.tolerance_seconds = tolerance_seconds

    def verify(self, message: InboundMessage, *, secret_ref: str, now_unix: int) -> bool:
        """Check freshness against the replay window.

        The cryptographic check runs at the execution boundary with the
        referenced secret; this contract bounds the window and rejects stale
        or future deliveries before any parsing occurs.
        """
        del secret_ref
        drift = abs(now_unix - message.delivered_at_unix)
        return drift <= self.tolerance_seconds


__all__ = [
    "ChannelConnectionStatus",
    "InboundMessage",
    "NormalizedMessage",
    "OutboundMessage",
    "SignatureVerifier",
]
