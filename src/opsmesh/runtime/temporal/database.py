from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from opsmesh.shared.config import Settings


@dataclass(frozen=True)
class TemporalDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def temporal_database(settings: Settings) -> AsyncIterator[TemporalDatabase]:
    database_url = make_url(settings.database_url).set(drivername="postgresql+psycopg")
    configured_options = database_url.query.get("options", "")
    if not isinstance(configured_options, str):
        raise ValueError("Database connection options must be a single string")
    options = (
        f"{configured_options} -c statement_timeout={settings.database_statement_timeout_ms}"
    ).strip()
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=settings.temporal_outbox_database_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args={"options": options},
    )
    try:
        yield TemporalDatabase(engine, async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
