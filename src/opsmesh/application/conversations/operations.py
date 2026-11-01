"""Authorized platform operations for reviewed conversations.

Run submission deliberately has no automatic reply target. A worker must review the
completed output before calling send. Cancelling a waiter never cancels its durable run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable

from opsmesh.framework.adapters import OpsMeshMessage
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.api import BotAccounts, DeliveryReceipt, MessageDelivery
from opsmesh.modules.bots.conversations import ConversationBatch, ConversationInbox
from opsmesh.modules.capabilities.api import KnowledgeDocument
from opsmesh.modules.capabilities.knowledge_management import KnowledgeManagement
from opsmesh.modules.memory.api import MemorySource
from opsmesh.modules.memory.management import MemoryManagement
from opsmesh.modules.runtime.api import (
    AgentRunView,
    ModelSettingsOverrides,
    RunOverrides,
)
from opsmesh.modules.runtime.management import RunManagement
from opsmesh.modules.sessions.management import ConversationManagement

from .configuration import ConversationBinding, ConversationOptions
from .media import ConversationMedia

SubmissionObserver = Callable[[str], Awaitable[None]]
_PURPOSE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


def _digest(parts: list[str]) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class ConversationOperations:
    def __init__(
        self,
        settings: ConversationOptions,
        queue: ConversationInbox,
        *,
        bots: BotAccounts,
        delivery: MessageDelivery,
        runtime: RunManagement,
        sessions: ConversationManagement,
        knowledge: KnowledgeManagement,
        memory: MemoryManagement,
        driver: OpsMeshDriver,
    ) -> None:
        self.settings, self.queue = settings, queue
        self.bots, self.delivery, self.runtime = bots, delivery, runtime
        self.sessions, self.knowledge, self.memory, self.driver = (
            sessions,
            knowledge,
            memory,
            driver,
        )
        # Only session creation/submission is serialized, never model execution.
        self._submission_lock = asyncio.Lock()

    def _validate_group(self, group: ConversationBinding) -> None:
        if self.settings.binding(group.bot_id, group.channel_id) != group:
            raise OpsMeshError(
                "FORBIDDEN", "Conversation operation requires an exact group binding"
            )

    @staticmethod
    def _validate_key(idempotency_key: str) -> None:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 1024:
            raise OpsMeshError("INVALID_REQUEST", "Conversation idempotency key is invalid")

    async def run(
        self,
        group: ConversationBinding,
        agent_ref: str,
        prompt: str | list[dict[str, object]],
        idempotency_key: str,
        *,
        purpose: str,
        existing_run_id: str | None = None,
        on_submitted: SubmissionObserver | None = None,
    ) -> AgentRunView:
        self._validate_group(group)
        self._validate_key(idempotency_key)
        if agent_ref != group.agent_ref or not _PURPOSE.fullmatch(purpose):
            raise OpsMeshError("FORBIDDEN", "Agent and purpose must belong to this conversation")
        if not (isinstance(prompt, str) and prompt.strip()) and not (
            isinstance(prompt, list) and prompt and all(isinstance(item, dict) for item in prompt)
        ):
            raise OpsMeshError("INVALID_REQUEST", "Conversation run input cannot be empty")
        operation = _digest(
            [
                group.bot_id,
                group.channel_id,
                group.principal_id,
                agent_ref,
                purpose,
                idempotency_key,
            ]
        )
        run_id = existing_run_id
        try:
            async with asyncio.timeout(self.settings.run_wait_seconds):
                run = await self._obtain_run(
                    group, agent_ref, prompt, operation, purpose, existing_run_id
                )
                run_id = run.id
                if on_submitted is not None:
                    await on_submitted(run.id)
                return await self._wait_for_run(group, agent_ref, operation, purpose, run)
        except TimeoutError:
            raise OpsMeshError(
                "CONVERSATION_RUN_TIMEOUT",
                "Waiting for the conversation run timed out; the durable run remains active",
                retryable=True,
                details={"run_id": run_id} if run_id is not None else {},
            ) from None

    async def _session(
        self, group: ConversationBinding, agent_ref: str, *, media_operation: str | None = None
    ) -> str:
        key = "conversation:session:" + _digest(
            [group.bot_id, group.channel_id, group.principal_id, agent_ref]
            + ([media_operation] if media_operation else [])
        )
        session = await self.sessions.get_or_open(
            key, agent_ref, workspace_id=group.workspace_id, actor_id=group.principal_id
        )
        if session.owner_id != group.principal_id or session.agent_ref != agent_ref:
            raise OpsMeshError(
                "FORBIDDEN", "Stored conversation session has a different owner or agent"
            )
        if session.workspace_id != group.workspace_id:
            raise OpsMeshError("CONFLICT", "Stored conversation session uses a different workspace")
        if session.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "The conversation session is closed")
        return session.id

    async def _obtain_run(
        self,
        group: ConversationBinding,
        agent_ref: str,
        prompt: str | list[dict[str, object]],
        operation: str,
        purpose: str,
        existing_run_id: str | None,
    ) -> AgentRunView:
        async with self._submission_lock:
            key = "conversation:run:" + operation
            intent = await self.queue.get_value(key)
            stored_run_id = intent.get("run_id") if intent is not None else None
            if existing_run_id and stored_run_id and existing_run_id != stored_run_id:
                raise OpsMeshError(
                    "CONFLICT", "Conversation operation already belongs to another run"
                )
            selected_run_id = existing_run_id or stored_run_id
            runtime = self.runtime
            if selected_run_id:
                run = await runtime.get_run(str(selected_run_id), actor_id=group.principal_id)
            else:
                session_id = (
                    str(intent["session_id"])
                    if intent is not None
                    else await self._session(
                        group, agent_ref, media_operation=operation if purpose == "media" else None
                    )
                )
                if intent is None:
                    await self.queue.set_value(key, {"session_id": session_id})
                existing = await runtime.find_submission(
                    "conversation-run:" + operation, actor_id=group.principal_id
                )
                run = (
                    existing
                    if existing is not None
                    else await runtime.submit(
                        actor_id=group.principal_id,
                        session_id=session_id,
                        agent_ref=agent_ref,
                        input=prompt,
                        overrides=RunOverrides(
                            tool_choice="none",
                            model_settings=ModelSettingsOverrides(max_tokens=2048),
                        )
                        if purpose == "media"
                        else RunOverrides(),
                        idempotency_key="conversation-run:" + operation,
                        metadata={
                            "conversation_channel": group.channel_id,
                            "conversation_bot": group.bot_id,
                            "purpose": purpose,
                            "conversation_operation": operation,
                        },
                    )
                )
            self._validate_run(group, agent_ref, operation, purpose, run)
            await self.queue.set_value(key, {"session_id": run.session_id, "run_id": run.id})
            return run

    @staticmethod
    def _validate_run(
        group: ConversationBinding,
        agent_ref: str,
        operation: str,
        purpose: str,
        run: AgentRunView,
    ) -> None:
        expected = {
            "conversation_channel": group.channel_id,
            "conversation_bot": group.bot_id,
            "purpose": purpose,
            "conversation_operation": operation,
        }
        if (
            run.principal_id != group.principal_id
            or (run.published_agent_ref or run.agent.ref) != agent_ref
            or any(run.metadata.get(key) != value for key, value in expected.items())
            or "reply_target" in run.metadata
            or (purpose == "media" and run.overrides.tool_choice != "none")
        ):
            raise OpsMeshError(
                "FORBIDDEN", "Run does not belong to the reviewed conversation workflow"
            )

    async def _wait_for_run(
        self,
        group: ConversationBinding,
        agent_ref: str,
        operation: str,
        purpose: str,
        run: AgentRunView,
    ) -> AgentRunView:
        from opsmesh.application.waiting import wait_for_run

        result = await wait_for_run(
            lambda: self.runtime.get_run(run.id, actor_id=group.principal_id),
            interval=self.settings.poll_seconds,
            initial=run,
            validate=lambda current: self._validate_run(
                group, agent_ref, operation, purpose, current
            ),
        )
        if result.status != "completed":
            raise OpsMeshError(
                "CONVERSATION_RUN_" + result.status.upper(),
                "Conversation run requires attention before a reply can be sent",
                retryable=result.status == "waiting_approval",
                details={"run_id": result.id, "status": result.status},
            )
        return result

    async def send(
        self,
        group: ConversationBinding,
        text: str,
        idempotency_key: str,
        *,
        message_id: str | None = None,
    ) -> DeliveryReceipt:
        """Send only text already approved by the worker, with durable delivery deduplication."""
        self._validate_group(group)
        self._validate_key(idempotency_key)
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > self.settings.reply_character_limit
        ):
            raise OpsMeshError("INVALID_REQUEST", "Conversation reply text is empty or too long")
        target = await self.bots.resolve_delivery_target(
            group.bot_id,
            group.channel_id,
            actor_id=group.principal_id,
            message_id=message_id,
        )
        return await self.delivery.send(
            target,
            OpsMeshMessage(text),
            idempotency_key="conversation-send:"
            + _digest([group.bot_id, group.channel_id, group.principal_id, idempotency_key]),
            actor_id=group.principal_id,
        )

    async def recall(self, group: ConversationBinding, message_id: str) -> None:
        """Recall inside the explicitly configured group; preserve official API failures."""
        self._validate_group(group)
        # Reuse the public authorization boundary before invoking a protocol-specific API.
        target = await self.bots.resolve_delivery_target(
            group.bot_id, group.channel_id, actor_id=group.principal_id, message_id=message_id
        )
        adapter = self.driver.adapters.get(target.bot.adapter_id)
        if adapter is None:
            raise OpsMeshError("BOT_ADAPTER_UNAVAILABLE", "Bot adapter is unavailable")
        adapter.ensure_enabled()
        bot = adapter.bots.get(group.bot_id)
        if bot is None or bot.ref != target.bot:
            raise OpsMeshError("BOT_UNAVAILABLE", "The configured bot is not connected")
        await bot.remove_message(target)

    async def capture(
        self, group: ConversationBinding, batch: ConversationBatch, answer: str, run_id: str
    ) -> None:
        """Store a reviewed, delivered transcript as non-authoritative source material."""
        self._validate_group(group)
        if group.knowledge_collection is None and group.memory_namespace_id is None:
            return
        stored = await self.queue.get_batch(batch.batch_id)
        if (
            stored != batch
            or stored.state != "replied"
            or not stored.delivery_id
            or stored.run_id != run_id
            or (stored.bot_id, stored.channel_id) != (group.bot_id, group.channel_id)
        ):
            raise OpsMeshError("CONFLICT", "Only confirmed delivered batches can be captured")
        media = ConversationMedia(self.settings, self.queue, self)
        text = json.dumps(
            {
                "record_type": "raw_conversation",
                "authoritative": False,
                "source_event_ids": [item.event_id for item in batch.messages],
                "questions": [(await media.rendered(item)).text for item in batch.messages],
                "delivered_answer": answer,
            },
            ensure_ascii=False,
        )
        if group.knowledge_collection:
            document = KnowledgeDocument(
                id="conversation-" + _digest([group.knowledge_collection, run_id]),
                collection=group.knowledge_collection,
                title="公开对话原始记录 (非权威资料)",
                text=text,
            )
            knowledge = self.knowledge
            try:
                await knowledge.add(document, principal_id=group.principal_id)
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
                previous = await knowledge.get_document(
                    document.id, principal_id=group.principal_id
                )
                if previous != document:
                    raise OpsMeshError(
                        "CONFLICT", "Conversation capture changed after delivery"
                    ) from None
        if group.memory_namespace_id:
            memory = self.memory
            await memory.append_source(
                MemorySource(
                    id="conversation-" + _digest([group.memory_namespace_id, run_id]),
                    namespace_id=group.memory_namespace_id,
                    conversation_id=group.key,
                    run_id=run_id,
                    text=text,
                ),
                principal_id=group.principal_id,
            )
            await memory.request_generation(
                group.memory_namespace_id,
                principal_id=group.principal_id,
                idempotency_key="conversation:" + run_id,
            )
