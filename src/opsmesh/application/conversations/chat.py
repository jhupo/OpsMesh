"""One immutable conversational batch per group; fresh messages wait for the next batch."""

from __future__ import annotations

import logging

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import ConversationBatch, ConversationInbox

from .configuration import ConversationBinding, ConversationOptions
from .contracts import ConversationPolicy, ReviewDecision, conversation_text
from .media import ConversationMedia
from .operations import ConversationOperations
from .transcript import conversation_input

logger = logging.getLogger(__name__)


class ConversationChatWorker:
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

    async def process(self, group: ConversationBinding, batch: ConversationBatch) -> None:
        if batch.oversized:
            await self.queue.complete_batch(batch.batch_id, state="review")
            return

        async def submitted(run_id: str) -> None:
            await self.queue.record_run(batch.batch_id, run_id)

        prompt = conversation_input(
            [await self.media.rendered(message) for message in batch.messages],
            purpose="chat",
            reply_character_limit=self.settings.reply_character_limit,
        )
        run = await self.platform.run(
            group,
            group.agent_ref,
            prompt,
            f"conversation:chat:{batch.batch_id}",
            purpose="chat",
            existing_run_id=batch.run_id,
            on_submitted=submitted,
        )
        answer = conversation_text(run.output)
        if not answer:
            await self.queue.complete_batch(batch.batch_id, run_id=run.id, state="observed")
            return
        if len(answer) > self.settings.reply_character_limit:
            await self.queue.complete_batch(batch.batch_id, run_id=run.id, state="review")
            return
        output_key = f"output-review:{batch.batch_id}"
        checked = await self.queue.get_value(output_key)
        if checked is None:
            review = await self.moderation.review(answer)
            checked = ReviewDecision.model_validate(review).model_dump(mode="json")
            await self.queue.set_value(output_key, checked)
        checked = ReviewDecision.model_validate(checked).model_dump(mode="json")
        if checked["status"] != "ok" or checked["action"] != "allow":
            await self.queue.complete_batch(batch.batch_id, run_id=run.id, state="review")
            return
        receipt = await self.platform.send(
            group,
            answer,
            f"conversation:reply:{batch.batch_id}",
            message_id=batch.messages[-1].message_id,
        )
        try:
            if group.knowledge_collection or group.memory_namespace_id:
                # This capture is retryable independently of the already confirmed send.
                await self.queue.set_value(
                    group.capture_prefix + batch.batch_id,
                    {"batch_id": batch.batch_id, "answer": answer, "run_id": run.id},
                )
            await self.queue.complete_batch(
                batch.batch_id, run_id=run.id, delivery_id=receipt.delivery_id, state="replied"
            )
        except Exception as error:
            # The public delivery service has already persisted a confirmed receipt.
            # Recovery uses the same delivery key; it cannot emit a second message.
            raise OpsMeshError(
                "CONVERSATION_COMMIT_PENDING",
                "Confirmed delivery awaits local conversation finalization",
                retryable=True,
                details={"run_id": run.id, "delivery_id": receipt.delivery_id},
            ) from error
        logger.info("conversation.replied batch=%s run=%s", batch.batch_id, run.id)
