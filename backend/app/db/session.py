from collections.abc import Generator
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401


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


def create_database_engine(settings: Settings) -> Engine:
    engine_kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "future": True,
    }
    if not _is_sqlite_url(settings.database_url):
        engine_kwargs.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout_seconds,
                "pool_recycle": settings.database_pool_recycle_seconds,
            }
        )
        if _is_postgres_url(settings.database_url):
            engine_kwargs["connect_args"] = {
                "options": (
                    f"-c statement_timeout={settings.database_statement_timeout_ms}"
                )
            }
    return create_engine(settings.database_url, **engine_kwargs)


def _is_sqlite_url(database_url: str) -> bool:
    return make_url(database_url).get_backend_name() == "sqlite"


def _is_postgres_url(database_url: str) -> bool:
    return make_url(database_url).get_backend_name().startswith("postgresql")


def database_pool_snapshot(target_engine: Engine | None = None) -> DatabasePoolSnapshot:
    resolved_engine = target_engine or engine
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


engine = create_database_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
