from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class DatabaseConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def commit_or_raise_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DatabaseConflictError(message) from exc
