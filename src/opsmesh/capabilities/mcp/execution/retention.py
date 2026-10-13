"""Expire bounded diagnostic details without deleting business call records."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.execution.diagnostics import DIAGNOSTIC_META_KEY
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.orchestration.runs.models import RunEvent


def expire_mcp_diagnostics(session: Session, *, days: int, limit: int = 100) -> int:
    rows = session.scalars(
        select(MCPToolCallLog)
        .where(
            MCPToolCallLog.created_at < datetime.now(UTC) - timedelta(days=days),
            or_(
                MCPToolCallLog.error["diagnostic"].is_not(None),
                MCPToolCallLog.response["_meta"][DIAGNOSTIC_META_KEY].is_not(None),
            ),
        )
        .order_by(MCPToolCallLog.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        if row.error is not None and "diagnostic" in row.error:
            row.error = {key: value for key, value in row.error.items() if key != "diagnostic"}
            row.error = {**row.error, "diagnostic_expired": True}
        if row.response is not None:
            metadata = row.response.get("_meta")
            if isinstance(metadata, dict) and DIAGNOSTIC_META_KEY in metadata:
                metadata = {
                    key: value for key, value in metadata.items() if key != DIAGNOSTIC_META_KEY
                }
                row.response = {**row.response, "_meta": metadata}
    events = session.scalars(
        select(RunEvent)
        .where(
            RunEvent.created_at < datetime.now(UTC) - timedelta(days=days),
            RunEvent.event_type == "mcp.diagnostic.notification",
            RunEvent.event_metadata["diagnostic"].is_not(None),
        )
        .order_by(RunEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for event in events:
        event.event_metadata = {
            key: value for key, value in event.event_metadata.items() if key != "diagnostic"
        }
        event.event_metadata = {**event.event_metadata, "diagnostic_expired": True}
    session.flush()
    return len(rows) + len(events)
