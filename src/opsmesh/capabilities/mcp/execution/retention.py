"""Expire bounded diagnostic details without deleting business call records."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.models import McpToolCallLog


def expire_mcp_diagnostics(session: Session, *, days: int, limit: int = 100) -> int:
    rows = session.scalars(
        select(McpToolCallLog)
        .where(
            McpToolCallLog.created_at < datetime.now(UTC) - timedelta(days=days),
            McpToolCallLog.error["diagnostic"].is_not(None),
        )
        .order_by(McpToolCallLog.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        if row.error is not None:
            row.error = {key: value for key, value in row.error.items() if key != "diagnostic"}
            row.error = {**row.error, "diagnostic_expired": True}
    session.flush()
    return len(rows)
