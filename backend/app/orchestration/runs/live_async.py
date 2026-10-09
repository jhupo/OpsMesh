"""Bounded, disposable live previews; publication never blocks the SDK event loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
from uuid import UUID, uuid4

from backend.app.agents.execution.contracts import AgentRunRequest
from backend.app.orchestration.runs.live_events import RunLivePublisher
from backend.app.orchestration.tasks.events import TaskEventPublisher
from backend.app.shared.concurrency import BlockingIO

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LivePublication:
    workspace_id: UUID
    task_id: UUID
    event_type: str
    event_id: str
    payload: dict[str, object]


class BufferedLiveBus:
    def __init__(self, bus: TaskEventPublisher, io: BlockingIO) -> None:
        self._bus = bus
        self._io = io
        self._pending: asyncio.Queue[LivePublication] = asyncio.Queue(maxsize=64)

    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        event_id: str | None = None,
        payload: dict[str, object] | None = None,
        outbox_id: str | None = None,
    ) -> str:
        if outbox_id is not None:
            raise ValueError("Durable outbox events cannot use the live preview buffer")
        event_id = event_id or str(uuid4())
        if self._pending.full():
            # Only live previews are disposable. Durable Run/Task events use the
            # PostgreSQL outbox and are never sent through this buffer.
            self._pending.get_nowait()
            self._pending.task_done()
        self._pending.put_nowait(
            LivePublication(workspace_id, task_id, event_type, event_id, payload or {})
        )
        return event_id

    async def pump(self) -> None:
        while True:
            item = await self._pending.get()
            try:
                await self._io.run(
                    partial(
                        self._bus.publish,
                        workspace_id=item.workspace_id,
                        task_id=item.task_id,
                        event_type=item.event_type,
                        event_id=item.event_id,
                        payload=item.payload,
                    )
                )
            except Exception:
                logger.warning("Live preview publication failed")
            finally:
                self._pending.task_done()

    async def drain(self) -> None:
        await self._pending.join()


@asynccontextmanager
async def buffered_live_events(
    request: AgentRunRequest, io: BlockingIO
) -> AsyncIterator[AgentRunRequest]:
    publisher = request.event_sink
    if not isinstance(publisher, RunLivePublisher):
        yield request
        return
    original = publisher.bus
    bus = BufferedLiveBus(original, io)
    publisher.bus = bus
    pump = asyncio.create_task(bus.pump(), name=f"live-preview:{request.context.run_id}")
    try:
        yield request
    finally:
        await bus.drain()
        pump.cancel()
        with suppress(asyncio.CancelledError):
            await pump
        publisher.bus = original
