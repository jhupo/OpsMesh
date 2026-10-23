"""Resumable SSE framing over an application-owned durable event log."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from fastapi import Request

from opsmesh.shared.errors import DomainError
from opsmesh.shared.serialization import canonical_json


@dataclass(frozen=True)
class EventBatch:
    events: list[tuple[int, str, dict[str, object]]]
    finished: bool


async def stream_events(
    request: Request, fetch: Callable[[int], EventBatch], *, after: int, lifetime_seconds: int = 300
) -> AsyncIterator[str]:
    deadline, heartbeat = time.monotonic() + lifetime_seconds, time.monotonic()
    yield "retry: 1000\n\n"
    while time.monotonic() < deadline and not await request.is_disconnected():
        try:
            batch = await asyncio.to_thread(fetch, after)
        except DomainError as exc:
            yield f"event: error\ndata: {canonical_json({'code': exc.code})}\n\n"
            return
        for sequence, kind, payload in batch.events:
            yield f"id: {sequence}\nevent: {kind}\ndata: {canonical_json(payload)}\n\n"
            after = sequence
        if batch.finished and len(batch.events) < 200:
            yield f"event: end\ndata: {canonical_json({'after': after})}\n\n"
            return
        if time.monotonic() - heartbeat >= 10:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        if len(batch.events) < 200:
            await asyncio.sleep(1)
    yield f"event: reconnect\ndata: {canonical_json({'after': after})}\n\n"
