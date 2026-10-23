"""One durable message dispatch; execution continues independently of sockets."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from opsmesh_sdk.channels import ContentPart

from opsmesh.gateway.contracts import GatewayReply
from opsmesh.gateway.messaging.commands import command
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.ports import ExecutionPort
from opsmesh.shared.errors import DomainError, InvalidArgumentError


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
        self, inbox: Inbox, outbox: Outbox, execution: ExecutionPort, media_root: Path
    ) -> None:
        self.inbox, self.outbox, self.execution = inbox, outbox, execution
        self.media_root = media_root

    async def process(self, inbox_id: UUID, owner: str) -> None:
        try:
            turn, run_id = await asyncio.to_thread(self.inbox.turn, inbox_id, owner)
            await self.execution.authorize(turn.user_id, turn.route)
            if run_id is None and not turn.policy.activates(turn.message):
                await asyncio.to_thread(self.inbox.passive, turn)
                return
            control = command(turn.message)
            if run_id is None and control:
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
            if turn.route.output_mode == "text":
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
                if turn.route.output_mode == "message":
                    try:
                        content = await GatewayMedia(
                            self.media_root / str(turn.channel_id)
                        ).prepare(
                            GatewayReply.parse(output).content,
                            preserve_image_url=turn.message.reply_address.get("kind")
                            in {"channel", "dm"},
                        )
                    except ValueError as error:
                        raise InvalidArgumentError(
                            "The route requires a typed gateway reply"
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
