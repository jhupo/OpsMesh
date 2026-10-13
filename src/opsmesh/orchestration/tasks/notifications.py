"""Wake local stream subscribers after durable PostgreSQL event commits."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from psycopg import AsyncConnection, sql
from sqlalchemy.engine import make_url

TASK_EVENT_NOTIFICATION_CHANNEL = "opsmesh_task_events"
logger = logging.getLogger(__name__)


class TaskEventNotifications:
    """One API listener fans out hints; PostgreSQL remains the replay source."""

    def __init__(self) -> None:
        self._subscribers: dict[tuple[UUID, UUID], set[asyncio.Event]] = defaultdict(set)

    @contextmanager
    def subscribe(self, workspace_id: UUID, task_id: UUID) -> Iterator[asyncio.Event]:
        key = workspace_id, task_id
        event = asyncio.Event()
        self._subscribers[key].add(event)
        try:
            yield event
        finally:
            subscribers = self._subscribers[key]
            subscribers.remove(event)
            if not subscribers:
                del self._subscribers[key]

    def wake(self, workspace_id: UUID, task_id: UUID) -> None:
        for subscriber in self._subscribers.get((workspace_id, task_id), ()):
            subscriber.set()

    def reconnect(self) -> None:
        # Re-read committed facts after reconnect; notifications are only hints.
        for subscribers in self._subscribers.values():
            for subscriber in subscribers:
                subscriber.set()


class TaskEventNotificationListener:
    def __init__(self, database_url: str, notifications: TaskEventNotifications) -> None:
        self._dsn = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self._notifications = notifications
        self.ready = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with await AsyncConnection.connect(
                    self._dsn, autocommit=True, connect_timeout=10
                ) as connection:
                    await connection.execute(
                        sql.SQL("LISTEN {}").format(sql.Identifier(TASK_EVENT_NOTIFICATION_CHANNEL))
                    )
                    self.ready.set()
                    self._notifications.reconnect()
                    async for notification in connection.notifies():
                        workspace_id, separator, task_id = notification.payload.partition(":")
                        if not separator:
                            continue
                        try:
                            self._notifications.wake(UUID(workspace_id), UUID(task_id))
                        except ValueError:
                            continue
                        if stop.is_set():
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ready.clear()
                logger.error(
                    "Task event notification connection failed",
                    extra={"error_code": type(exc).__name__},
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    continue
