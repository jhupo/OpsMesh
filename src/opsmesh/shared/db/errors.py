"""Map database constraint failures to neutral conflicts."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class DatabaseConflictError(Exception):
    """A unique or foreign-key constraint rejected the staged write."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def commit_or_raise_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DatabaseConflictError(message) from exc


def flush_or_raise_conflict(session: Session, message: str) -> None:
    """Translate a constraint failure; the caller owns rollback of its transaction."""
    try:
        session.flush()
    except IntegrityError as exc:
        raise DatabaseConflictError(message) from exc
