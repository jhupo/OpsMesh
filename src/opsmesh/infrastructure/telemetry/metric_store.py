"""Shared database snapshots preserve short-lived worker observations across processes."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from math import isfinite
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore

from .metrics import GAUGES, MetricsSnapshot, PlatformMetrics

logger = logging.getLogger(__name__)


class PersistentMetricStore:
    def __init__(
        self,
        database: AsyncDatabase,
        metrics: PlatformMetrics,
        *,
        flush_interval_seconds: float = 5,
        live_seconds: float = 30,
        io_timeout_seconds: float = 2,
    ) -> None:
        if any(
            not isfinite(value) or value <= 0
            for value in (flush_interval_seconds, live_seconds, io_timeout_seconds)
        ):
            raise ValueError("Metric intervals and deadlines must be finite and positive")
        self.records = VersionedRecordStore(database, "telemetry_metric_snapshots")
        self.metrics = metrics
        self.instance_id = uuid4().hex
        self.flush_interval_seconds = flush_interval_seconds
        self.live_seconds = live_seconds
        self.io_timeout_seconds = io_timeout_seconds
        self._lock = asyncio.Lock()
        self._closed = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None and not self._closed:
            await self._best_effort_flush()
            self._task = asyncio.create_task(self._heartbeat(), name="metrics-snapshot")

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_seconds)
            await self._best_effort_flush()

    async def _best_effort_flush(self) -> None:
        try:
            async with asyncio.timeout(self.io_timeout_seconds):
                await self.flush()
        except Exception:
            logger.exception("Metrics snapshot could not be persisted")

    async def flush(self) -> None:
        async with self._lock:
            payload = {
                "snapshot": self.metrics.snapshot().model_dump(mode="json"),
                "active": not self._closed,
                "expires_at": time.time() + self.live_seconds if not self._closed else 0,
            }
            for _ in range(5):
                current = await self.records.get(self.instance_id)
                try:
                    if current is None:
                        await self.records.create(self.instance_id, payload)
                    else:
                        await self.records.update(self.instance_id, payload, current.revision)
                    return
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
            raise OpsMeshError("CONFLICT", "Metrics snapshot changed concurrently")

    async def render(self) -> str:
        try:
            async with asyncio.timeout(self.io_timeout_seconds):
                await self.flush()
                records = await self.records.list()
        except TimeoutError as error:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Shared metric snapshots are unavailable", retryable=True
            ) from error
        aggregate = PlatformMetrics()
        gauges: dict[str, tuple[float, float]] = {}
        now = time.time()
        for record in records:
            snapshot = MetricsSnapshot.model_validate(record.data["snapshot"])
            aggregate.include_snapshot(snapshot)
            if record.data.get("active") and record.data.get("expires_at", 0) > now:
                for name, value in snapshot.gauges.items():
                    timestamp = snapshot.gauge_timestamps.get(name, record.updated_at.timestamp())
                    if timestamp + self.live_seconds <= now:
                        continue
                    if name not in gauges or timestamp > gauges[name][0]:
                        gauges[name] = (timestamp, value)
        # These gauges are whole-platform observations. Summing coordinators would duplicate them.
        for name in GAUGES:
            aggregate.gauge(name, gauges.get(name, (0, 0))[1])
        return aggregate.render()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._best_effort_flush()
