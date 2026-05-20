from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401


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


engine = create_database_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
