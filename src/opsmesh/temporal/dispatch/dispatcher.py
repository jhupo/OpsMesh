"""Notification-driven delivery; timers only retry committed undelivered commands."""

import asyncio
import logging
from datetime import UTC, datetime

from psycopg import AsyncConnection, sql
from sqlalchemy.engine import make_url

from opsmesh.temporal.dispatch.outbox import (
    DISPATCH_NOTIFICATION_CHANNEL,
    TemporalOutboxPublisher,
)

logger = logging.getLogger(__name__)
DEFAULT_RECOVERY_INTERVAL_SECONDS = 60


class TemporalOutboxDispatcher:
    def __init__(
        self,
        publisher: TemporalOutboxPublisher,
        *,
        database_url: str,
        recovery_interval_seconds: float = DEFAULT_RECOVERY_INTERVAL_SECONDS,
    ) -> None:
        self._publisher = publisher
        self._database_dsn = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self._recovery_interval = recovery_interval_seconds
        self._wakeup = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        async with asyncio.TaskGroup() as tasks:
            listener = tasks.create_task(self._listen(stop), name="temporal-outbox-listener")
            try:
                await self._publish(stop)
            finally:
                listener.cancel()

    async def _listen(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with await AsyncConnection.connect(
                    self._database_dsn, autocommit=True, connect_timeout=10
                ) as connection:
                    await connection.execute(
                        sql.SQL("LISTEN {}").format(sql.Identifier(DISPATCH_NOTIFICATION_CHANNEL))
                    )
                    # The publisher's recovery scan catches commits before LISTEN too.
                    self._wakeup.set()
                    async for _ in connection.notifies():
                        self._wakeup.set()
                        if stop.is_set():
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Temporal outbox notification connection failed",
                    extra={"error_code": type(exc).__name__},
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    continue

    async def _publish(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._wakeup.clear()
            try:
                while await self._publisher.publish_pending() and not stop.is_set():
                    pass
                next_delivery = await self._publisher.next_delivery_at()
                delay = self._recovery_interval
                if next_delivery is not None:
                    delay = min(delay, max(0, (next_delivery - datetime.now(UTC)).total_seconds()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Temporal outbox recovery failed",
                    extra={"error_code": type(exc).__name__},
                )
                delay = self._recovery_interval
            wakeup = asyncio.create_task(self._wakeup.wait())
            stopping = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait(
                    (wakeup, stopping), timeout=delay, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                wakeup.cancel()
                stopping.cancel()
                await asyncio.gather(wakeup, stopping, return_exceptions=True)
