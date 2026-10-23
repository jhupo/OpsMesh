"""Persisted lifecycle events with monotonically increasing per-run sequence."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.execution.runs.models import ExecutionEvent
from opsmesh.shared.security.encryption import SecretCipher


def append_event(
    session: Session, *, run_id: UUID, kind: str, payload: dict[str, object], cipher: SecretCipher
) -> None:
    # The caller holds the Run row lock, which serializes sequence allocation.
    sequence = (
        session.scalar(
            select(func.max(ExecutionEvent.sequence)).where(ExecutionEvent.run_id == run_id)
        )
        or 0
    )
    encrypted = cipher.encrypt_payload(payload)
    session.add(
        ExecutionEvent(
            run_id=run_id,
            sequence=sequence + 1,
            kind=kind,
            encrypted_payload=encrypted.ciphertext,
            key_id=encrypted.key_id,
        )
    )
    session.flush()
