"""Batch native SDK text deltas into the durable, owned Run event log."""

import asyncio
import time
from uuid import UUID

from agents.stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from openai.types.responses import ResponseReasoningSummaryTextDeltaEvent, ResponseTextDeltaEvent
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.execution.preparation import AgentSnapshot
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher


def public_agent_ids(snapshot: AgentSnapshot) -> set[str]:
    def descendants(roots: set[str], *, tools: bool) -> set[str]:
        result, pending = set[str](), list(roots)
        while pending:
            identifier = pending.pop()
            if identifier in result:
                continue
            result.add(identifier)
            config = snapshot.definitions[identifier]
            pending.extend(item.agent_id for item in config.handoffs)
            if tools:
                pending.extend(item.agent_id for item in config.tools if item.type == "agent")
        return result

    internal = {
        tool.agent_id
        for config in snapshot.definitions.values()
        for tool in config.tools
        if tool.type == "agent"
    }
    return descendants({snapshot.root_id}, tools=False) - descendants(internal, tools=True)


class RunEventRecorder:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        run_id: UUID,
        *,
        attempt_id: str | None = None,
    ) -> None:
        self.sessions, self.cipher, self.run_id = sessions, cipher, run_id
        self.parts: list[str] = []
        self.size = 0
        self.flushed_at = time.monotonic()
        self.attempt_id = attempt_id

    async def write(self, kind: str, payload: dict[str, object]) -> None:
        def persist() -> None:
            with self.sessions.begin() as session:
                row = session.get(ExecutionRun, self.run_id, with_for_update=True)
                if row is None or row.status not in {"running", "cancelling"}:
                    raise ConflictError("The Run is not accepting model events")
                if self.attempt_id is not None and row.attempt_id != self.attempt_id:
                    raise ConflictError("The model event belongs to another execution attempt")
                append_event(session, run_id=row.id, kind=kind, payload=payload, cipher=self.cipher)

        await asyncio.to_thread(persist)

    async def flush(self) -> None:
        if self.parts:
            text = "".join(self.parts)
            await self.write("model_text_delta", {"delta": text})
            self.parts.clear()
            self.size = 0
            self.flushed_at = time.monotonic()

    async def __call__(self, event: object) -> None:
        if isinstance(event, RawResponsesStreamEvent):
            await self.raw(event.data)
        elif isinstance(event, AgentUpdatedStreamEvent):
            await self.flush()
            await self.write("agent_updated", {"name": event.new_agent.name})
        elif isinstance(event, RunItemStreamEvent):
            await self.flush()
            await self.write("agent_item", {"name": event.name, "item_type": event.item.type})

    async def raw(self, event: object) -> None:
        if isinstance(event, ResponseTextDeltaEvent):
            self.parts.append(event.delta)
            self.size += len(event.delta)
            if self.size >= 2048 or time.monotonic() - self.flushed_at >= 0.1:
                await self.flush()
        elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
            await self.flush()
            await self.write("model_summary_delta", {"delta": event.delta})
        else:
            await self.flush()
