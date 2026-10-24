"""One durable message dispatch; execution continues independently of sockets."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from opsmesh_sdk.channels import ContentPart
from opsmesh_sdk.messages import MessageFilterResult

from opsmesh.gateway.contracts import GatewayReply
from opsmesh.gateway.messaging.commands import command
from opsmesh.gateway.messaging.filters import FilterDecision, MessageFilters
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.ports import ExecutionPort
from opsmesh.shared.errors import DomainError, ForbiddenError, InvalidArgumentError


def reply_parts(output: object) -> list[ContentPart]:
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    if len(text) > 100_000:
        suffix = "\n[Output truncated. View the full result in OpsMesh.]"
        text = text[: 100_000 - len(suffix)] + suffix
    return [
        ContentPart(text=text[offset : offset + 1500]) for offset in range(0, len(text), 1500)
    ] or [ContentPart(text="Task completed.")]


class Dispatcher:
    def __init__(
        self,
        inbox: Inbox,
        outbox: Outbox,
        execution: ExecutionPort,
        media_root: Path,
        filters: MessageFilters | None = None,
    ) -> None:
        self.inbox, self.outbox, self.execution = inbox, outbox, execution
        self.media_root = media_root
        self.filters = filters

    async def process(self, inbox_id: UUID, owner: str) -> None:
        try:
            turn, run_id = await asyncio.to_thread(self.inbox.turn, inbox_id, owner)
            if turn.status not in {"pending", "ready", "dispatching", "submitted", "responding"}:
                return
            if turn.status == "responding":
                if run_id is None:
                    run_id = await self.execution.submit_rejection(turn)
                status, output = await self.execution.result(turn, run_id)
                if status == "completed":
                    if not isinstance(output, str) or not output.strip() or len(output) > 1500:
                        raise InvalidArgumentError("The rejection Agent must return bounded text")
                    await asyncio.to_thread(self.outbox.complete, turn, [ContentPart(text=output)])
                elif status in {"failed", "cancelled", "interrupted", "needs_attention"}:
                    await asyncio.to_thread(
                        self.inbox.fail, inbox_id, owner, "rejection_generation_failed"
                    )
                return
            if turn.status == "pending":
                try:
                    if turn.policy.message_filters and self.filters is None:
                        raise InvalidArgumentError(
                            "Message filtering is not configured on this host"
                        )
                    decision = (
                        await self.filters.evaluate(turn)
                        if self.filters
                        else FilterDecision(MessageFilterResult(action="allow"))
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await asyncio.to_thread(self.inbox.filter_failed, turn, type(error).__name__)
                    return
                result = decision.result
                if result.action != "allow":
                    await asyncio.to_thread(self.outbox.reject, turn, result, decision.origin)
                    return
                await asyncio.to_thread(self.inbox.approved, turn, result)
            if run_id is None and not turn.policy.activates(turn.message):
                await asyncio.to_thread(self.inbox.ignore, turn)
                return
            await self.execution.authorize(turn.user_id, turn.target)
            if run_id is None:
                claimed = await asyncio.to_thread(self.inbox.claim, turn)
                if claimed is None:
                    return
                turn = claimed
            control = command(turn.message)
            if run_id is None and control:
                if (
                    control != "help"
                    and turn.message.conversation.kind != "private"
                    and turn.message.actor.external_role
                    not in turn.policy.group_for(turn.message).control_roles
                ):
                    raise ForbiddenError("Conversation control requires an authorized group role")
                value = (
                    "/help /new /reset /cancel"
                    if control == "help"
                    else await self.execution.control(turn, control)
                )
                await asyncio.to_thread(self.outbox.complete, turn, reply_parts(value))
                return
            if run_id is None:
                run_id = await self.execution.submit(turn)
                await asyncio.to_thread(self.inbox.submitted, turn, run_id)
            status, output = await self.execution.result(turn, run_id)
            if turn.target.output_mode == "text":
                if (
                    turn.policy.streaming
                    or turn.policy.show_tool_hints
                    or turn.policy.show_thinking
                ):
                    batch = await self.execution.events(turn, run_id, turn.event_cursor)
                    await asyncio.to_thread(self.outbox.events, turn, batch)
                if status not in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                    "needs_attention",
                }:
                    await asyncio.to_thread(self.outbox.progress, turn)
            if status == "completed":
                if turn.target.output_mode == "message":
                    try:
                        reply = GatewayReply.parse(output)
                        content = await GatewayMedia(
                            self.media_root / str(turn.channel_id)
                        ).prepare(
                            reply.content,
                            preserve_image_url=turn.message.reply_address.get("kind")
                            in {"channel", "dm"},
                        )
                    except ValueError as error:
                        raise InvalidArgumentError(
                            "The channel target requires a typed gateway reply"
                        ) from error
                else:
                    content = reply_parts(output)
                await asyncio.to_thread(self.outbox.complete, turn, content)
            elif status in {"failed", "cancelled", "interrupted", "needs_attention"}:
                await asyncio.to_thread(
                    self.outbox.complete,
                    turn,
                    reply_parts(f"Task {run_id} status: {status}. Open OpsMesh to continue."),
                )
        except DomainError as error:
            await asyncio.to_thread(self.inbox.fail, inbox_id, owner, type(error).__name__)
