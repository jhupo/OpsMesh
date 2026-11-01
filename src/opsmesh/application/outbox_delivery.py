"""Outbox delivery ownership, lease renewal and acknowledgement."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext, suppress
from typing import Any
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.infrastructure.telemetry.tracing import ApplicationTraceExporter

logger = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class OutboxDelivery:
    def __init__(
        self,
        database: AsyncDatabase,
        metrics: PlatformMetrics,
        handlers: tuple[EventHandler, ...],
        traces: Callable[[], ApplicationTraceExporter | None],
    ) -> None:
        self.database, self.metrics, self.handlers, self.traces = (
            database,
            metrics,
            handlers,
            traces,
        )
        self.worker_id = uuid4().hex

    async def deliver(self, fact: dict[str, Any]) -> None:
        for handler in self.handlers:
            await handler(fact)

    async def deliver_events(self) -> None:
        owner = f"{self.worker_id}:{uuid4().hex}"
        events = await self.database.claim_events(owner, limit=10, lease_seconds=120)
        await asyncio.gather(*(self._deliver_claim(event, owner) for event in events))

    async def _deliver_claim(self, event: dict[str, Any], owner: str) -> None:
        database = self.database
        try:
            await self._deliver_with_lease(event, owner)
            await database.acknowledge_event(event["id"], owner=owner)
            self.metrics.increment("outbox_events")
        except asyncio.CancelledError:
            raise
        except Exception:
            with suppress(OpsMeshError):  # A lost lease belongs to the next delivery attempt.
                await database.reject_event(event["id"], owner=owner)
            self.metrics.increment("outbox_events", "failure")
            logger.exception("Outbox delivery failed", extra={"event_id": event["id"]})

    async def _deliver_with_lease(self, event: dict[str, Any], owner: str) -> None:
        finished = asyncio.Event()

        async def renew() -> None:
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=30)
                except TimeoutError:
                    await self.database.renew_event_lease(event["id"], owner, 120)

        async def deliver() -> None:
            try:
                traces = self.traces()
                scope = (
                    traces.span(
                        "event.deliver",
                        carrier=event.get("trace_context", {}),
                        attributes={"opsmesh.event.type": event["type"]},
                    )
                    if traces
                    else nullcontext()
                )
                with scope:
                    await self.deliver(event)
            finally:
                finished.set()

        async with asyncio.TaskGroup() as group:
            group.create_task(renew())
            group.create_task(deliver())
