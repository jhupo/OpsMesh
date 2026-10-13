from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.tasks.models import TaskEventRecord


@dataclass(frozen=True)
class TaskEvent:
    id: str
    workspace_id: UUID
    task_id: UUID
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at,
        }
        if self.event_id is not None:
            event["event_id"] = self.event_id
        return event


class TaskEventReader(Protocol):
    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
    ) -> list[TaskEvent]: ...

    def contains_cursor(self, workspace_id: UUID, task_id: UUID, cursor: str) -> bool: ...


class PostgresTaskEventReader:
    """Read durable task events directly from PostgreSQL for replay and streaming."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
    ) -> list[TaskEvent]:
        statement = select(TaskEventRecord).where(
            TaskEventRecord.workspace_id == workspace_id,
            TaskEventRecord.task_id == task_id,
        )
        if after_id != "0":
            try:
                cursor = UUID(after_id)
            except ValueError as exc:
                raise ValueError("Task event cursor is invalid") from exc
            previous = self._session.scalar(
                select(TaskEventRecord).where(
                    TaskEventRecord.workspace_id == workspace_id,
                    TaskEventRecord.task_id == task_id,
                    TaskEventRecord.event_id == cursor,
                )
            )
            if previous is None:
                return []
            statement = statement.where(TaskEventRecord.sequence > previous.sequence)
        rows = self._session.scalars(
            statement.order_by(TaskEventRecord.sequence).limit(max(1, count))
        ).all()
        return [
            TaskEvent(
                id=str(row.event_id),
                workspace_id=row.workspace_id,
                task_id=row.task_id,
                event_type=row.event_type,
                payload=dict(row.payload),
                created_at=row.created_at,
                event_id=str(row.event_id),
            )
            for row in rows
        ]

    def contains_cursor(self, workspace_id: UUID, task_id: UUID, cursor: str) -> bool:
        try:
            event_id = UUID(cursor)
        except ValueError:
            return False
        return (
            self._session.scalar(
                select(TaskEventRecord.id).where(
                    TaskEventRecord.workspace_id == workspace_id,
                    TaskEventRecord.task_id == task_id,
                    TaskEventRecord.event_id == event_id,
                )
            )
            is not None
        )
