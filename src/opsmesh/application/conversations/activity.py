"""Conservative proactive participation with durable attempt reservations.

The coordinator calls tick while holding the same per-group lock used by chat.
An attempt consumes its quota before any model or delivery call. An uncertain
delivery is never retried, and the same source message cannot trigger it again.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import ConversationInbox, QueuedConversationMessage

from .configuration import ConversationBinding, ConversationOptions
from .contracts import ConversationPolicy, ReviewDecision, conversation_text

if TYPE_CHECKING:
    from .operations import ConversationOperations
from .transcript import conversation_input


class _ActivityState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    attempts: int = Field(default=0, ge=0)
    last_attempt: AwareDatetime | None = None
    last_source_event_id: str | None = None
    attempt_key: str | None = None
    outcome: Literal[
        "startup", "reserved", "observed", "running", "review", "stale", "sending", "sent", "failed"
    ] = "startup"
    run_id: str | None = None
    delivery_id: str | None = None
    error_code: str | None = None


class ConversationActivity:
    def __init__(
        self,
        settings: ConversationOptions,
        queue: ConversationInbox,
        platform: ConversationOperations,
        moderation: ConversationPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings, self.queue, self.platform, self.moderation = (
            settings,
            queue,
            platform,
            moderation,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._started_at = self._clock()
        self._timezone = ZoneInfo(settings.timezone)
        self._initialized: set[str] = set()
        self._locks = {group.key: asyncio.Lock() for group in settings.bindings}

    @staticmethod
    def _key(group: ConversationBinding) -> str:
        return "activity:" + hashlib.sha256(group.key.encode()).hexdigest()[:32]

    def _quiet(self, now: datetime) -> bool:
        hour = now.astimezone(self._timezone).hour
        start, end = self.settings.quiet_start_hour, self.settings.quiet_end_hour
        if start == end:
            return False
        return start <= hour < end if start < end else hour >= start or hour < end

    async def _state(self, group: ConversationBinding, now: datetime) -> _ActivityState:
        saved = await self.queue.get_value(self._key(group))
        state = (
            _ActivityState.model_validate(saved)
            if saved
            else _ActivityState(day=now.astimezone(self._timezone).date().isoformat())
        )
        if group.key not in self._initialized:
            # Every process restart starts a cooldown without resetting the day's quota.
            state.last_attempt = max(state.last_attempt or self._started_at, self._started_at)
            await self._save(group, state)
            self._initialized.add(group.key)
        return state

    async def _save(self, group: ConversationBinding, state: _ActivityState) -> None:
        await self.queue.set_value(self._key(group), state.model_dump(mode="json"))

    async def _busy(self, group: ConversationBinding) -> bool:
        channel = (group.bot_id, group.channel_id)
        if channel in await self.queue.ready_channels():
            return True
        if any(
            (batch.bot_id, batch.channel_id) == channel
            for batch in await self.queue.pending_batches()
        ):
            return True
        return any(
            (message.bot_id, message.channel_id) == channel
            for message in await self.queue.pending_moderation(limit=1024)
        )

    async def _recent(
        self, group: ConversationBinding, now: datetime
    ) -> tuple[QueuedConversationMessage, ...]:
        messages = await self.queue.recent_messages(
            group.bot_id, group.channel_id, limit=self.settings.activity_history_limit
        )
        cutoff = now - timedelta(seconds=self.settings.proactive_recent_activity_seconds)
        selected: list[QueuedConversationMessage] = []
        size = 0
        for message in reversed(messages):
            if not cutoff <= message.occurred_at <= now + timedelta(seconds=30):
                continue
            if not message.text.strip() or message.has_attachments:
                continue
            length = len(message.text) + bool(selected)
            if size + length > self.settings.batch_characters:
                break
            selected.append(message)
            size += length
        return tuple(reversed(selected))

    async def tick(self, group: ConversationBinding) -> None:
        if not group.proactive_enabled or self.settings.proactive_daily_limit == 0:
            return
        lock = self._locks.get(group.key)
        if lock is None or self.settings.binding(group.bot_id, group.channel_id) != group:
            raise ValueError("Activity group must be a configured binding")
        if lock.locked():
            return
        async with lock:
            await self._tick(group)

    async def _tick(self, group: ConversationBinding) -> None:
        now = self._clock()
        state = await self._state(group, now)
        if self._quiet(now) or await self._busy(group):
            return
        today = now.astimezone(self._timezone).date().isoformat()
        if state.day == today and state.attempts >= self.settings.proactive_daily_limit:
            return
        interval = self.settings.proactive_interval_seconds
        last_reply = await self.queue.last_reply(group.bot_id, group.channel_id)
        if any(
            value is not None and (now - value).total_seconds() < interval
            for value in (state.last_attempt, last_reply)
        ):
            return
        activity = await self.queue.last_activity(group.bot_id, group.channel_id)
        if (
            activity is None
            or not 0
            <= (now - activity).total_seconds()
            <= self.settings.proactive_recent_activity_seconds
        ):
            return
        messages = await self._recent(group, now)
        if not messages or messages[-1].event_id == state.last_source_event_id:
            return
        state.attempts = state.attempts + 1 if state.day == today else 1
        state.day, state.last_attempt = today, now
        state.last_source_event_id = messages[-1].event_id
        state.attempt_key = f"{self._key(group)}:{today}:{state.attempts}"
        state.outcome, state.run_id, state.delivery_id, state.error_code = (
            "reserved",
            None,
            None,
            None,
        )
        await self._save(group, state)
        try:
            await self._participate(group, state, messages, activity)
        except asyncio.CancelledError:
            # The durable reservation remains consumed, including cancellation during send.
            raise
        except Exception as error:
            state.outcome = "failed"
            state.error_code = error.code if isinstance(error, OpsMeshError) else "ACTIVITY_FAILED"
            await self._save(group, state)

    async def _participate(
        self,
        group: ConversationBinding,
        state: _ActivityState,
        messages: tuple[QueuedConversationMessage, ...],
        activity: datetime,
    ) -> None:
        texts = [message.text for message in messages]
        if await self._stale(group, activity):
            state.outcome = "stale"
            await self._save(group, state)
            return
        assert state.attempt_key is not None

        async def submitted(run_id: str) -> None:
            state.run_id, state.outcome = run_id, "running"
            await self._save(group, state)

        run = await self.platform.run(
            group,
            group.agent_ref,
            self._prompt(messages),
            state.attempt_key + ":run",
            purpose="activity",
            on_submitted=submitted,
        )
        state.run_id = run.id
        if run.status != "completed":
            state.outcome = "review"
            await self._save(group, state)
            return
        text = conversation_text(run.output)
        if not text:
            state.outcome = "observed"
            await self._save(group, state)
            return
        if len(text) > self.settings.reply_character_limit:
            state.outcome = "review"
            await self._save(group, state)
            return
        review = ReviewDecision.model_validate(await self.moderation.review(text, context=texts))
        if review.status != "ok" or review.action != "allow":
            state.outcome = "review"
            await self._save(group, state)
            return
        if await self._stale(group, activity):
            state.outcome = "stale"
            await self._save(group, state)
            return
        state.outcome = "sending"
        await self._save(group, state)
        receipt = await self.platform.send(
            group, text, state.attempt_key + ":send", message_id=None
        )
        state.delivery_id, state.outcome = receipt.delivery_id, "sent"
        await self._save(group, state)
        await self.queue.record_reply(group.bot_id, group.channel_id)

    async def _stale(self, group: ConversationBinding, activity: datetime) -> bool:
        now = self._clock()
        last_reply = await self.queue.last_reply(group.bot_id, group.channel_id)
        return (
            self._quiet(now)
            or (now - activity).total_seconds() > self.settings.proactive_recent_activity_seconds
            or (
                last_reply is not None
                and (now - last_reply).total_seconds() < self.settings.proactive_interval_seconds
            )
            or await self.queue.last_activity(group.bot_id, group.channel_id) != activity
            or await self._busy(group)
        )

    def _prompt(self, messages: tuple[QueuedConversationMessage, ...]) -> str:
        return conversation_input(
            messages,
            purpose="activity",
            reply_character_limit=self.settings.reply_character_limit,
        )
