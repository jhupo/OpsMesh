"""Transactional native history with private Run staging and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.orchestration.sessions.models import (
    SessionHistoryBatch,
    SessionHistoryItem,
    SessionRecord,
)
from opsmesh.shared.errors import ConflictError


def prepare_run(session: Session, *, model_session: SessionRecord, run_id: UUID) -> None:
    """Prepare the FIFO head's staging batch; retries and approval reuse it."""
    batch = session.scalar(select(SessionHistoryBatch).where(SessionHistoryBatch.run_id == run_id))
    if batch is None:
        session.add(
            SessionHistoryBatch(
                session_id=model_session.id,
                run_id=run_id,
            )
        )
        session.flush()
    elif batch.session_id != model_session.id:
        raise ConflictError("The Run history batch belongs to another Session")


def run_batch(session: Session, *, session_id: UUID, run_id: UUID) -> SessionHistoryBatch:
    batch = session.scalar(select(SessionHistoryBatch).where(SessionHistoryBatch.run_id == run_id))
    if batch is None or batch.session_id != session_id:
        raise ConflictError("The Run history batch was not prepared")
    return batch


def read_items(
    session: Session,
    *,
    session_id: UUID,
    limit: int | None = None,
    run_id: UUID | None = None,
) -> list[dict[str, Any]]:
    statement = select(SessionHistoryItem).where(SessionHistoryItem.session_id == session_id)
    if run_id is None:
        statement = statement.where(SessionHistoryItem.committed_revision.is_not(None))
    else:
        batch = run_batch(session, session_id=session_id, run_id=run_id)
        statement = statement.where(
            SessionHistoryItem.committed_revision.is_not(None)
            | (SessionHistoryItem.batch_id == batch.id)
        )
    statement = statement.order_by(
        SessionHistoryItem.committed_revision.asc().nulls_last(), SessionHistoryItem.position
    )
    rows = list(session.scalars(statement))
    if limit is not None:
        rows = rows[-limit:]
    return [dict(row.payload) for row in rows]


def append_items(
    session: Session, *, batch: SessionHistoryBatch, items: list[dict[str, Any]]
) -> None:
    if batch.committed_revision is not None:
        raise ConflictError("The Run history batch is already committed")
    last = session.scalar(
        select(func.max(SessionHistoryItem.position)).where(SessionHistoryItem.batch_id == batch.id)
    )
    position = 0 if last is None else last + 1
    for offset, item in enumerate(items):
        session.add(
            SessionHistoryItem(
                session_id=batch.session_id,
                batch_id=batch.id,
                position=position + offset,
                payload=item,
            )
        )
    session.flush()


def pop_item(
    session: Session, *, session_id: UUID, batch: SessionHistoryBatch | None
) -> dict[str, Any] | None:
    statement = select(SessionHistoryItem).where(SessionHistoryItem.session_id == session_id)
    if batch is not None:
        if batch.committed_revision is not None:
            raise ConflictError("The Run history batch is already committed")
        statement = statement.where(SessionHistoryItem.batch_id == batch.id)
    else:
        statement = statement.where(SessionHistoryItem.committed_revision.is_not(None))
    item = session.scalar(
        statement.order_by(
            SessionHistoryItem.committed_revision.desc().nulls_last(),
            SessionHistoryItem.position.desc(),
        ).limit(1)
    )
    if item is None:
        return None
    payload = dict(item.payload)
    session.delete(item)
    session.flush()
    return payload


def clear_items(session: Session, *, session_id: UUID, batch: SessionHistoryBatch | None) -> None:
    if batch is not None:
        raise ConflictError("A Run cannot clear committed Session history")
    session.execute(delete(SessionHistoryItem).where(SessionHistoryItem.session_id == session_id))


def publish_batch(
    session: Session, *, model_session: SessionRecord, batch: SessionHistoryBatch
) -> int:
    """Publish items, receipt and revision atomically, without copying native items."""
    if batch.committed_revision is not None:
        return batch.committed_revision
    model_session.history_revision += 1
    batch.committed_revision = model_session.history_revision
    session.execute(
        update(SessionHistoryItem)
        .where(SessionHistoryItem.batch_id == batch.id)
        .values(committed_revision=batch.committed_revision)
    )
    session.flush()
    return batch.committed_revision


def commit_run(session: Session, *, model_session: SessionRecord, run_id: UUID) -> int:
    return publish_batch(
        session,
        model_session=model_session,
        batch=run_batch(session, session_id=model_session.id, run_id=run_id),
    )


@dataclass(frozen=True)
class HistoryGateway:
    """Shared reads use the same PostgreSQL storage as staged Run writes."""

    sessions: sessionmaker[Session]

    async def items(self, session_id: str, *, limit: int | None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return read_items(session, session_id=UUID(session_id), limit=limit)
