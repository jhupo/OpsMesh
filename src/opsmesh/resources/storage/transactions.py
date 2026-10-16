"""Transaction-bound content staging: blobs follow their transaction's fate.

A content blob is written before the database row that references it, and
a deletion removes the row before the blob. The session events are the
compensation point in both directions: a rollback deletes exactly the
blobs that transaction staged and keeps the ones it meant to remove, and
a commit does the opposite. Without this, a failed transaction leaves
orphan blobs no row can reference, and a rolled-back deletion destroys
content the database still records.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID
from weakref import WeakKeyDictionary, WeakSet

from sqlalchemy import event
from sqlalchemy.orm import Session

from opsmesh.resources.storage.filesystem import delete_content

_STAGED_WRITES: WeakKeyDictionary[Session, dict[UUID, Path]] = WeakKeyDictionary()
_STAGED_REMOVALS: WeakKeyDictionary[Session, dict[UUID, Path]] = WeakKeyDictionary()
_LISTENING: WeakSet[Session] = WeakSet()


def stage_content(session: Session, storage_root: Path, content_id: UUID) -> None:
    """Register one already-written blob for rollback cleanup.

    Callers must run inside a transaction boundary that commits or rolls
    back the session; without one the staged file lives until the session
    is collected.
    """
    staged = _STAGED_WRITES.setdefault(session, {})
    staged[content_id] = storage_root
    _listen(session)


def stage_content_removal(session: Session, storage_root: Path, content_id: UUID) -> None:
    """Register one blob for deletion once the transaction commits."""
    staged = _STAGED_REMOVALS.setdefault(session, {})
    staged[content_id] = storage_root
    _listen(session)


def _listen(session: Session) -> None:
    if session not in _LISTENING:
        event.listen(session, "after_commit", _on_commit)
        event.listen(session, "after_rollback", _on_rollback)
        _LISTENING.add(session)


def _on_commit(session: Session) -> None:
    """A committed transaction keeps its staged blobs and removes the rest."""
    _STAGED_WRITES.pop(session, None)
    removals = _STAGED_REMOVALS.pop(session, None) or {}
    for content_id, storage_root in removals.items():
        _delete_quietly(storage_root, content_id)


def _on_rollback(session: Session) -> None:
    """A rolled-back transaction deletes exactly what it staged."""
    _STAGED_REMOVALS.pop(session, None)
    staged = _STAGED_WRITES.pop(session, None) or {}
    for content_id, storage_root in staged.items():
        _delete_quietly(storage_root, content_id)


def _delete_quietly(storage_root: Path, content_id: UUID) -> None:
    try:
        delete_content(storage_root, content_id=content_id)
    except OSError:
        # Compensation must not mask the outcome that triggered it; a blob
        # that is already gone is harmless.
        return
