"""Serialize archive writers and explicit cleanup without an open transaction."""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from opsmesh.shared.errors import DomainError


@contextmanager
def archive_export_ownership(session: Session, job_id: UUID) -> Iterator[None]:
    bind = session.get_bind()
    engine: Engine = bind.engine if isinstance(bind, Connection) else bind
    key = int.from_bytes(sha256(b"archive-export:" + job_id.bytes).digest()[:8], "big", signed=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if not connection.scalar(select(func.pg_try_advisory_lock(key))):
            raise DomainError(
                "Archive export is still executing", code="archive_export_busy", status_code=409
            )
        try:
            yield
        finally:
            try:
                connection.execute(select(func.pg_advisory_unlock(key)))
            except SQLAlchemyError:
                connection.invalidate()
