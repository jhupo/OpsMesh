from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from opsmesh_operator.contracts import Contract, ReleaseManifest
from opsmesh_operator.files import atomic_write


class UpdatePlan(Contract):
    action: Literal["update", "rollback", "backup"]
    target: ReleaseManifest
    previous: ReleaseManifest
    database_revision: str
    configuration_sha256: str
    expires_at: datetime
    maintenance_required: Literal[True] = True
    database_restore_requires_confirmation: Literal[True] = True

    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class JournalEvent(Contract):
    id: UUID
    phase: str
    created_at: datetime


class Journal(Contract):
    job_id: UUID
    plan: UpdatePlan
    phase: str
    backup_id: str | None = None
    history: list[JournalEvent] = []

    def save(self, root: Path) -> None:
        atomic_write(root / "updates" / f"{self.job_id}.json", self.model_dump_json(indent=2))

    def advance(self, root: Path, phase: str, *, backup_id: str | None = None) -> Journal:
        journal = self.model_copy(
            update={
                "phase": phase,
                "history": [
                    *self.history,
                    JournalEvent(id=uuid4(), phase=phase, created_at=datetime.now(UTC)),
                ],
                "backup_id": backup_id or self.backup_id,
            }
        )
        journal.save(root)
        return journal
