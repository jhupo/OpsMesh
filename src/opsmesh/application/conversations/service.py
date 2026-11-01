"""Public composition of a durable, reviewed conversation pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from opsmesh.framework import on_message
from opsmesh.framework.adapters import MessageEvent
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext
from opsmesh.infrastructure.coordination.process_lock import LocalProcessLock
from opsmesh.modules.bots.conversations import (
    ConversationInbox,
    QueuedConversationMessage,
)

from .configuration import ConversationOptions
from .contracts import ConversationPolicy
from .coordinator import ConversationCoordinator
from .media import retain_attachments
from .operations import ConversationOperations

logger = logging.getLogger(__name__)


class ConversationApplication:
    """Coordinate one inbox consumer with platform authorization for external operations.

    Register before application startup. The caller owns the policy provider's lifecycle.
    This application never creates another Agent, model, client, knowledge collection or
    tool. Bindings refer to resources already provisioned through the platform API.
    """

    def __init__(
        self,
        options: ConversationOptions,
        policy: ConversationPolicy,
        inbox: ConversationInbox,
        *,
        operations: ConversationOperations,
        driver: OpsMeshDriver,
    ) -> None:
        self.options, self.policy = options, policy
        self.queue = inbox
        self.operations, self.driver = operations, driver
        self.coordinator = ConversationCoordinator(options, self.queue, self.operations, policy)
        self._lock = LocalProcessLock(options.data_directory / "consumer.lock")
        self.ready = asyncio.Event()
        self._startup_complete = asyncio.Event()
        self._registered = False
        self._closed = False

    def register(self) -> None:
        """Install lifecycle hooks and an exact-binding matcher ahead of automatic replies."""
        if self._registered:
            raise OpsMeshError("CONFLICT", "Conversation application is already registered")
        driver = self.driver
        driver.on_startup(self.start)
        driver.on_shutdown(self.close)
        matcher = on_message(rule=self.matches, priority=1, block=True)

        @matcher.handle()
        async def receive(context: DispatchContext[MessageEvent]) -> None:
            await self.receive(context)
            # Prevent the default per-message Agent route from bypassing output review.
            await matcher.finish()

        self._registered = True

    async def start(self) -> None:
        if self.ready.is_set():
            raise OpsMeshError("CONFLICT", "Conversation application is already started")
        self._closed = False
        self._startup_complete.clear()
        try:
            self._lock.acquire()
            await self._validate_active_bindings()
            self.coordinator.start(self.driver)
            self.ready.set()
            self._startup_complete.set()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._closed = True
        self.ready.clear()
        self._startup_complete.set()
        try:
            await self.coordinator.close()
        finally:
            self._lock.release()

    async def matches(self, context: DispatchContext[MessageEvent]) -> bool:
        target = context.reply_target
        return bool(
            target is not None
            and self.options.binding(target.bot.bot_id, target.channel_id) is not None
        )

    async def receive(self, context: DispatchContext[MessageEvent]) -> None:
        if not await self.matches(context):
            raise OpsMeshError("FORBIDDEN", "Message has no configured conversation binding")
        if self._closed:
            raise OpsMeshError("UNAVAILABLE", "Conversation application is stopped")
        if not self.ready.is_set():
            await self._startup_complete.wait()
        if self._closed or not self.ready.is_set():
            raise OpsMeshError("UNAVAILABLE", "Conversation application did not finish startup")
        target = context.reply_target
        assert target is not None
        if not target.message_id:
            raise OpsMeshError("INVALID_REQUEST", "Message requires a provider message identity")
        text = context.event.get_plaintext()
        attachments = tuple(
            segment
            for segment in context.event.get_message()
            if segment.type.rsplit(":", 1)[-1] not in {"text", "mention"}
        )
        attachment = bool(attachments)
        if len(text) > 16000:
            text, attachment = "", True
            logger.warning("conversation.oversized_input event=%s", context.event.id)
        else:
            await retain_attachments(
                self.queue, context.event.id, target.bot.bot_id, target.channel_id, attachments
            )
        await self.queue.enqueue(
            QueuedConversationMessage(
                event_id=context.event.id,
                bot_id=target.bot.bot_id,
                channel_id=target.channel_id,
                user_id=context.event.get_user_id(),
                message_id=target.message_id,
                text=text,
                occurred_at=context.event.occurred_at,
                to_me=context.event.is_tome(),
                has_attachments=attachment,
            )
        )

    async def _validate_active_bindings(self) -> None:
        """Reject configuration changes that would reinterpret an active durable batch."""
        key = "conversation:binding-snapshot"
        requested = {
            item.key: {
                "bot_id": item.bot_id,
                "channel_id": item.channel_id,
                "principal_id": item.principal_id,
                "agent_ref": item.agent_ref,
                "workspace_id": item.workspace_id,
                "knowledge_collection": item.knowledge_collection,
                "memory_namespace_id": item.memory_namespace_id,
            }
            for item in self.options.bindings
        }
        previous = await self.queue.get_value(key)
        for batch in await self.queue.pending_batches():
            binding = self.options.binding(batch.bot_id, batch.channel_id)
            if (
                binding is None
                or previous is None
                or previous.get(binding.key) != requested[binding.key]
            ):
                raise OpsMeshError(
                    "CONVERSATION_BINDING_BUSY",
                    "Finish or explicitly reconcile active batches before changing their binding",
                    details={"batch_id": batch.batch_id},
                )
        for binding_key, original in (previous or {}).items():
            if original == requested.get(binding_key):
                continue
            prefix = (
                "conversation-capture:" + hashlib.sha256(binding_key.encode()).hexdigest() + ":"
            )
            if await self.queue.list_values(prefix, limit=1):
                raise OpsMeshError(
                    "CONVERSATION_BINDING_BUSY",
                    "Finish pending knowledge captures before changing their binding",
                )
        await self.queue.set_value(key, requested)
