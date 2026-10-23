"""Engine, session factories and caller-owned transaction boundaries."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import sqlalchemy
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.shared.db.errors import DatabaseConflictError

POSTGRES_BACKEND = "postgresql"
DEFAULT_POOL_TIMEOUT_SECONDS = 30
DEFAULT_POOL_RECYCLE_SECONDS = 1800
DEFAULT_STATEMENT_TIMEOUT_MS = 30000


@contextmanager
def session_transaction(
    session: Session, *, conflict_message: str | None = None
) -> Iterator[Session]:
    """Caller-selected boundary for all staged work; does not close the session.

    This commits the current transaction, including changes staged before entry.
    Use only at the application operation boundary, never inside domain helpers.
    """
    try:
        yield session
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if conflict_message is not None:
            raise DatabaseConflictError(conflict_message) from exc
        raise
    except BaseException:
        session.rollback()
        raise


@contextmanager
def database_transaction(sessions: Callable[[], Session]) -> Iterator[Session]:
    """Commit staged changes, rollback any failure, and close the session."""
    with sessions() as session, session_transaction(session):
        yield session


@dataclass(frozen=True)
class DatabasePoolSnapshot:
    backend: str
    pool_class: str
    pool_size: int | None
    checked_in: int | None
    checked_out: int | None
    overflow: int | None
    max_overflow: int | None
    status: str

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "backend": self.backend,
            "pool_class": self.pool_class,
            "pool_size": self.pool_size,
            "checked_in": self.checked_in,
            "checked_out": self.checked_out,
            "overflow": self.overflow,
            "max_overflow": self.max_overflow,
            "status": self.status,
        }


def create_database_engine(
    url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: int = DEFAULT_POOL_TIMEOUT_SECONDS,
    pool_recycle_seconds: int = DEFAULT_POOL_RECYCLE_SECONDS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> Engine:
    database_url = make_url(url)
    if database_url.get_backend_name() != POSTGRES_BACKEND:
        raise ValueError("OpsMesh requires PostgreSQL")
    options = database_url.query.get("options", "")
    if not isinstance(options, str):
        raise ValueError("PostgreSQL options must have a single value")
    statement_timeout = f"-c statement_timeout={statement_timeout_ms}"
    return sqlalchemy.create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_recycle=pool_recycle_seconds,
        connect_args={"options": f"{options} {statement_timeout}".strip()},
    )


def database_pool_snapshot(resolved_engine: Engine) -> DatabasePoolSnapshot:
    pool = resolved_engine.pool
    return DatabasePoolSnapshot(
        backend=resolved_engine.url.get_backend_name(),
        pool_class=pool.__class__.__name__,
        pool_size=_pool_int(pool, "size"),
        checked_in=_pool_int(pool, "checkedin"),
        checked_out=_pool_int(pool, "checkedout"),
        overflow=_pool_int(pool, "overflow"),
        max_overflow=_pool_attr_int(pool, "_max_overflow"),
        status=pool.status(),
    )


def _pool_int(pool: object, method_name: str) -> int | None:
    method = getattr(pool, method_name, None)
    if method is None:
        return None
    try:
        return int(method())
    except (TypeError, ValueError, NotImplementedError):
        return None


def _pool_attr_int(pool: object, attr_name: str) -> int | None:
    value = getattr(pool, attr_name, None)
    return value if isinstance(value, int) else None
