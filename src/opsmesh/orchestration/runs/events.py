"""Composition-owned state and explicit execution operations."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import FlowEvent, FlowRun, Run, RunEvent
from opsmesh.orchestration.runs.schemas import EventPage, EventView
from opsmesh.shared.security.encryption import SecretCipher


def append_flow_event(session: Session, run: FlowRun, kind: str, summary: dict[str, Any]) -> None:
    run.revision += 1
    session.add(FlowEvent(run_id=run.id, sequence=run.revision, kind=kind, summary=summary))


def append_event(
    session: Session,
    *,
    run: Run,
    cipher: SecretCipher,
    event_key: str,
    kind: str,
    payload: dict[str, object],
) -> None:
    existing = session.scalar(select(RunEvent).where(RunEvent.event_key == event_key))
    if existing is not None:
        return
    run.event_sequence += 1
    session.add(
        RunEvent(
            scope_id=run.scope_id,
            run_id=run.id,
            sequence=run.event_sequence,
            event_key=event_key,
            kind=kind,
            payload_ciphertext=cipher.encrypt_payload(payload).ciphertext,
            created_at=datetime.now(UTC),
        )
    )
    session.flush()


def read_events(
    session: Session,
    *,
    run: Run,
    cipher: SecretCipher,
    after: int,
    limit: int,
) -> EventPage:
    rows = list(
        session.scalars(
            select(RunEvent)
            .where(
                RunEvent.run_id == run.id,
                RunEvent.sequence > after,
            )
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
    )
    return EventPage(
        events=[
            EventView(
                sequence=row.sequence,
                kind=row.kind,
                payload=cipher.decrypt_payload(row.payload_ciphertext),
            )
            for row in rows
        ],
        next_cursor=rows[-1].sequence if rows else after,
    )
