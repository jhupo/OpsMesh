"""Asynchronous PostgreSQL reader for durable task event streams."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from opsmesh.orchestration.tasks.events import TaskEvent
from opsmesh.orchestration.tasks.models import TaskEventRecord
from opsmesh.orchestration.tasks.notifications import TaskEventNotifications
from opsmesh.shared.config import Settings


class TaskEventStreamUnavailableError(RuntimeError):
    """The PostgreSQL event stream could not be read."""


def create_task_event_sessions(
    settings: Settings,
) -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
]:
    """Create the API's shared asynchronous event query pool."""
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() != "postgresql":
        raise ValueError("Durable task event streaming requires PostgreSQL")
    database_url = database_url.set(drivername="postgresql+psycopg")
    existing_options = database_url.query.get("options", "")
    if not isinstance(existing_options, str):
        raise ValueError("PostgreSQL options must be a string")
    options = (
        f"{existing_options} -c statement_timeout={settings.database_statement_timeout_ms}".strip()
    )
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=settings.temporal_outbox_database_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args={"options": options},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@dataclass(frozen=True)
class AsyncPostgresTaskEventReader:
    """Read committed task events without copying them to a transient stream."""

    sessions: async_sessionmaker[AsyncSession]
    notifications: TaskEventNotifications

    async def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        wait_seconds: float = 0.0,
    ) -> list[TaskEvent]:
        # Subscribe before reading to close the commit/read/wait race.
        with self.notifications.subscribe(workspace_id, task_id) as wakeup:
            events = await self._read_once(
                workspace_id=workspace_id, task_id=task_id, after_id=after_id, count=count
            )
            if events or wait_seconds <= 0:
                return events
            with suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), timeout=wait_seconds)
            return await self._read_once(
                workspace_id=workspace_id, task_id=task_id, after_id=after_id, count=count
            )

    async def contains_cursor(
        self,
        workspace_id: UUID,
        task_id: UUID,
        cursor: str,
    ) -> bool:
        event_id = _parse_cursor(cursor)
        if event_id is None:
            return cursor == "0"
        try:
            async with self.sessions() as session:
                value = await session.scalar(
                    select(TaskEventRecord.id).where(
                        TaskEventRecord.workspace_id == workspace_id,
                        TaskEventRecord.task_id == task_id,
                        TaskEventRecord.event_id == event_id,
                    )
                )
                return value is not None
        except SQLAlchemyError as exc:
            raise TaskEventStreamUnavailableError from exc

    async def _read_once(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int,
    ) -> list[TaskEvent]:
        event_id = _parse_cursor(after_id)
        if after_id != "0" and event_id is None:
            raise ValueError("Task event cursor must be a UUID or 0")
        try:
            async with self.sessions() as session:
                statement = select(TaskEventRecord).where(
                    TaskEventRecord.workspace_id == workspace_id,
                    TaskEventRecord.task_id == task_id,
                )
                if event_id is not None:
                    previous = await session.scalar(
                        select(TaskEventRecord).where(
                            TaskEventRecord.workspace_id == workspace_id,
                            TaskEventRecord.task_id == task_id,
                            TaskEventRecord.event_id == event_id,
                        )
                    )
                    if previous is None:
                        return []
                    statement = statement.where(TaskEventRecord.sequence > previous.sequence)
                rows = (
                    await session.scalars(
                        statement.order_by(
                            TaskEventRecord.sequence,
                        ).limit(max(1, count))
                    )
                ).all()
                return [_event_from_row(row) for row in rows]
        except SQLAlchemyError as exc:
            raise TaskEventStreamUnavailableError from exc


def _parse_cursor(value: str) -> UUID | None:
    if value == "0":
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _event_from_row(row: TaskEventRecord) -> TaskEvent:
    event_id = str(row.event_id)
    return TaskEvent(
        id=event_id,
        workspace_id=row.workspace_id,
        task_id=row.task_id,
        event_type=row.event_type,
        payload=dict(row.payload),
        created_at=row.created_at,
        event_id=event_id,
    )
