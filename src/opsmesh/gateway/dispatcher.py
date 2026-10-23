"""One durable message dispatch; execution continues independently of sockets."""

import asyncio
import json
from uuid import UUID

from opsmesh_sdk.channels import ContentPart

from opsmesh.gateway.commands import command
from opsmesh.gateway.inbox import Inbox
from opsmesh.gateway.outbox import Outbox
from opsmesh.gateway.ports import ExecutionPort
from opsmesh.shared.errors import DomainError


def reply_parts(output: object) -> list[ContentPart]:
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    if len(text) > 100_000:
        text = text[:100_000] + "\n[Output truncated. View the full result in OpsMesh.]"
    return [
        ContentPart(text=text[offset : offset + 1500]) for offset in range(0, len(text), 1500)
    ] or [ContentPart(text="Task completed.")]


class Dispatcher:
    def __init__(self, inbox: Inbox, outbox: Outbox, execution: ExecutionPort) -> None:
        self.inbox, self.outbox, self.execution = inbox, outbox, execution

    async def process(self, inbox_id: UUID, owner: str) -> None:
        try:
            turn, run_id = await asyncio.to_thread(self.inbox.turn, inbox_id, owner)
            await self.execution.authorize(turn.user_id, turn.route)
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
            if status == "completed":
                await asyncio.to_thread(self.outbox.complete, turn, reply_parts(output))
            elif status in {"failed", "cancelled", "interrupted", "needs_attention"}:
                await asyncio.to_thread(
                    self.outbox.complete,
                    turn,
                    reply_parts(f"Task {run_id} status: {status}. Open OpsMesh to continue."),
                )
        except DomainError as error:
            await asyncio.to_thread(self.inbox.fail, inbox_id, owner, type(error).__name__)
