"""Bounded deletion of expired login sessions; live sessions are never deleted."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.auth.models import LoginSession


def cleanup_expired_sessions(
    session: Session, *, cutoff: datetime, cursor: str | None, limit: int
) -> tuple[int, str | None]:
    cutoff = min(cutoff, datetime.now(UTC))
    query = select(LoginSession).where(LoginSession.expires_at < cutoff)
    if cursor:
        query = query.where(LoginSession.id > UUID(cursor))
    rows = list(session.scalars(query.order_by(LoginSession.id).limit(limit).with_for_update()))
    for row in rows:
        session.delete(row)
    return len(rows), str(rows[-1].id) if len(rows) == limit else None
