"""Persistent conversation inbox contracts; no database or protocol dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

BatchState = Literal["claimed", "running", "replied", "observed", "review", "failed"]
TerminalBatchState = Literal["replied", "observed", "review"]
ATTACHMENT_TEXT_LIMIT = 3000


@dataclass(frozen=True, slots=True)
class QueuedConversationMessage:
    event_id: str
    bot_id: str
    channel_id: str
    user_id: str
    message_id: str
    text: str
    occurred_at: datetime
    to_me: bool
    has_attachments: bool = False

    def __post_init__(self) -> None:
        for value in (self.event_id, self.bot_id, self.channel_id, self.user_id, self.message_id):
            if not isinstance(value, str) or not value or len(value) > 2048:
                raise ValueError("Inbox identifiers must contain 1 to 2048 characters")
        if not isinstance(self.text, str) or len(self.text) > 16_000:
            raise ValueError("A queued message may contain at most 16000 text characters")
        if type(self.to_me) is not bool or type(self.has_attachments) is not bool:
            raise ValueError("Message flags must be booleans")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Inbox timestamps must include a timezone")


@dataclass(frozen=True, slots=True)
class ConversationBatch:
    batch_id: str
    bot_id: str
    channel_id: str
    messages: tuple[QueuedConversationMessage, ...]
    state: BatchState
    created_at: datetime
    updated_at: datetime
    run_id: str | None = None
    delivery_id: str | None = None
    error_code: str | None = None
    oversized: bool = False


class ConversationInbox(Protocol):
    """One scoped inbox, consumed by one process under an external ownership lock."""

    async def enqueue(self, message: QueuedConversationMessage) -> bool: ...
    async def has_message(self, event_id: str) -> bool: ...
    async def pending_moderation(
        self, limit: int = 64, *, has_attachments: bool | None = None
    ) -> tuple[QueuedConversationMessage, ...]: ...
    async def set_moderation(
        self, event_id: str, decision: dict[str, Any], eligible_for_chat: bool
    ) -> None: ...
    async def ready_channels(self) -> tuple[tuple[str, str], ...]: ...
    async def claim_batch(
        self, bot_id: str, channel_id: str, *, max_chars: int = 12_000
    ) -> ConversationBatch | None: ...
    async def get_batch(self, batch_id: str) -> ConversationBatch: ...
    async def pending_batches(
        self, *, include_failed: bool = False
    ) -> tuple[ConversationBatch, ...]: ...
    async def record_run(self, batch_id: str, run_id: str) -> None: ...
    async def complete_batch(
        self,
        batch_id: str,
        *,
        run_id: str | None = None,
        delivery_id: str | None = None,
        state: TerminalBatchState = "replied",
    ) -> None: ...
    async def record_error(self, batch_id: str, error_code: str) -> None: ...
    async def mark_failed(self, batch_id: str, error_code: str) -> None: ...
    async def recent_messages(
        self, bot_id: str, channel_id: str, *, limit: int = 24
    ) -> tuple[QueuedConversationMessage, ...]: ...
    async def known_channels(self) -> tuple[tuple[str, str], ...]: ...
    async def last_activity(self, bot_id: str, channel_id: str) -> datetime | None: ...
    async def last_reply(self, bot_id: str, channel_id: str) -> datetime | None: ...
    async def record_reply(self, bot_id: str, channel_id: str) -> None: ...
    async def get_value(self, key: str) -> dict[str, Any] | None: ...
    async def set_value(self, key: str, value: dict[str, Any]) -> None: ...
    async def list_values(
        self, prefix: str, *, limit: int = 128, after: str | None = None
    ) -> tuple[tuple[str, dict[str, Any]], ...]: ...
    async def delete_value(self, key: str) -> None: ...
    async def purge_before(self, cutoff: datetime) -> int: ...


class ConversationInboxProvider(Protocol):
    def open(self, namespace: str) -> ConversationInbox: ...
