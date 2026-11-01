"""Fast moderation lane, independent of conversational model latency."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import ConversationInbox, QueuedConversationMessage

from .configuration import ConversationBinding, ConversationOptions
from .contracts import ConversationPolicy, ReviewDecision
from .media import ConversationMedia
from .operations import ConversationOperations

logger = logging.getLogger(__name__)


class ConversationModerationWorker:
    def __init__(
        self,
        settings: ConversationOptions,
        queue: ConversationInbox,
        platform: ConversationOperations,
        moderation: ConversationPolicy,
    ) -> None:
        self.settings, self.queue = settings, queue
        self.platform, self.moderation = platform, moderation
        self.media = ConversationMedia(settings, queue, platform)
        self._semaphore = asyncio.Semaphore(settings.moderation_concurrency)

    async def tick(self) -> None:
        pending = await self.queue.pending_moderation(
            limit=self.settings.moderation_concurrency,
            has_attachments=False if self.settings.images_enabled else None,
        )
        await asyncio.gather(*(self._review(message) for message in pending))

    async def media_tick(self) -> None:
        if self.settings.images_enabled:
            pending = await self.queue.pending_moderation(limit=1, has_attachments=True)
            if pending:
                await self._review(pending[0])

    async def _review(self, message: QueuedConversationMessage) -> None:
        async with self._semaphore:
            try:
                await self.review(message)
            except Exception as error:
                logger.error(
                    "conversation.moderation_failed event=%s code=%s",
                    message.event_id,
                    error.code if isinstance(error, OpsMeshError) else type(error).__name__,
                )

    async def review(self, message: QueuedConversationMessage) -> None:
        group = self.settings.binding(message.bot_id, message.channel_id)
        if group is None:
            return
        key = f"moderation:{message.event_id}"
        saved = await self.queue.get_value(key)
        if saved is not None:
            decision = ReviewDecision.model_validate_json(json.dumps(saved["decision"]))
        else:
            text = (
                await self.media.prepare(group, message)
                if message.has_attachments
                else message.text
            )
            decision = await self._decision(group.bot_id, group.channel_id, message.event_id, text)
        state = saved or {"decision": decision.model_dump(mode="json"), "recall": "new"}
        await self.queue.set_value(key, state)
        await self._apply(group, message, key, state, decision)

    async def _decision(
        self, bot_id: str, channel_id: str, event_id: str, text: str | None
    ) -> ReviewDecision:
        if not text or not text.strip():
            decision = ReviewDecision(
                status="unknown",
                action="review",
                reason="Attachments require moderator review",
                reason_code="ATTACHMENT_REQUIRES_REVIEW",
            )
        else:
            recent = await self.queue.recent_messages(bot_id, channel_id, limit=8)
            context = tuple(item.text for item in recent if item.event_id != event_id)
            # A bounded suffix of whole messages; never silently truncate a target message.
            while sum(map(len, context)) > 12000:
                context = context[1:]
            decision = ReviewDecision.model_validate(
                await self.moderation.review(text, context=context)
            )
        return decision

    async def _apply(
        self,
        group: ConversationBinding,
        message: QueuedConversationMessage,
        key: str,
        state: dict[str, Any],
        decision: ReviewDecision,
    ) -> None:
        if decision.status == "ok" and decision.action == "remove":
            age = (datetime.now(UTC) - message.occurred_at).total_seconds()
            if state["recall"] == "new":
                if not 0 <= age < self.settings.recall_window_seconds:
                    state["recall"] = "expired"
                else:
                    # A crash after this point is an uncertain side effect. Never blindly repeat it.
                    state["recall"] = "attempting"
                    await self.queue.set_value(key, state)
                    try:
                        await self.platform.recall(group, message.message_id)
                    except OpsMeshError as error:
                        state.update(recall="failed", error_code=error.code)
                        if error.info.provider_code:
                            state["provider_code"] = error.info.provider_code
                    else:
                        state["recall"] = "confirmed"
                await self.queue.set_value(key, state)
            if state["recall"] == "confirmed":
                notice = self.settings.removed_notice.format(reason=decision.reason)
            else:
                notice = self.settings.unconfirmed_notice.format(reason=decision.reason)
            if notice:
                try:
                    receipt = await self.platform.send(
                        group, notice, f"conversation:notice:{message.event_id}"
                    )
                    state["notice_delivery_id"] = receipt.delivery_id
                except OpsMeshError as error:
                    state["notice_error_code"] = error.code
            await self.queue.set_value(key, state)
        await self.queue.set_moderation(
            message.event_id, decision.model_dump(mode="json"), eligible_for_chat=True
        )
        logger.info(
            "conversation.moderated event=%s action=%s reason=%s recall=%s",
            message.event_id,
            decision.action,
            decision.reason_code,
            state["recall"],
        )
