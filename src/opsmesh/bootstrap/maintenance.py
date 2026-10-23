"""Explicit host maintenance; each bounded page commits its own effects and audit."""

import json
from datetime import UTC, datetime
from typing import Literal

from opsmesh.bootstrap.connections import build_database_resources
from opsmesh.config import Settings
from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.users.auth.maintenance import cleanup_expired_sessions


def maintain_database(
    settings: Settings,
    command: Literal["cleanup-login-sessions"],
    *,
    before: datetime | None = None,
) -> dict[str, object]:
    cutoff = before or datetime.now(UTC)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("The cleanup timestamp requires a timezone")
    if command != "cleanup-login-sessions":
        raise ValueError("Unknown maintenance command")
    database = build_database_resources(settings)
    cursor = None
    processed = 0
    try:
        while True:
            with database.sessions() as session, session.begin():
                count, next_cursor = cleanup_expired_sessions(
                    session, cutoff=cutoff, cursor=cursor, limit=100
                )
                record_audit(
                    session,
                    AuditRecord(
                        category="operation",
                        action=command,
                        actor=AuditActor(actor_type="platform"),
                        source="platform",
                        summary="Host maintenance batch completed",
                        payload_json=json.dumps({"processed": count}),
                    ),
                )
            processed += count
            if next_cursor is None:
                return {"command": command, "processed": processed}
            cursor = next_cursor
    finally:
        database.engine.dispose()
