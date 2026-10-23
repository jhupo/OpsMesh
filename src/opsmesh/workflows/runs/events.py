"""Encrypted workflow events appended under the owning run's transaction lock."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.workflows.runs.models import WorkflowEvent


def append_event(
    session: Session, *, run_id: UUID, kind: str, payload: dict[str, object], cipher: SecretCipher
) -> None:
    sequence = (
        session.scalar(
            select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id)
        )
        or 0
    ) + 1
    encrypted = cipher.encrypt_payload(payload)
    session.add(
        WorkflowEvent(
            run_id=run_id,
            sequence=sequence,
            kind=kind,
            encrypted_payload=encrypted.ciphertext,
            key_id=encrypted.key_id,
        )
    )
    session.flush()
