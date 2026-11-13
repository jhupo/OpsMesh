"""Own the process-wide async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from opsmesh import on_shutdown, on_startup
from opsmesh.plugins.config import get_plugin_config

from .config import Settings
from .errors import DatabaseUnavailableError


class Database:
    """Create the engine once and hand out short-lived sessions."""

    def __init__(self, settings: Settings) -> None:
        """Bind connection settings without creating an engine."""
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._closing = False

    @property
    def settings(self) -> Settings:
        """Return the immutable settings used to create the engine."""
        return self._settings

    @property
    def started(self) -> bool:
        """Whether the engine exists and accepts new sessions."""
        return self._engine is not None and not self._closing

    async def startup(self) -> None:
        """Create the engine and session factory without connecting."""
        if self._closing:
            raise DatabaseUnavailableError(
                "The database has unfinished cleanup"
            )
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._settings.dsn,
            echo=self._settings.echo,
            pool_size=self._settings.pool_size,
            max_overflow=self._settings.max_overflow,
            pool_timeout=self._settings.pool_timeout,
            pool_recycle=self._settings.pool_recycle,
            pool_pre_ping=True,
        )
        try:
            self._sessionmaker = async_sessionmaker(
                self._engine, expire_on_commit=False
            )
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        """Dispose the connection pool and forget the engine."""
        self._closing = True
        self._sessionmaker = None
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._closing = False

    @property
    def engine(self) -> AsyncEngine:
        """Return the started engine shared by all plugins."""
        if not self.started:
            raise DatabaseUnavailableError(
                "The database plugin has not started"
            )
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        """Return the factory used to open per-scope sessions."""
        if self._sessionmaker is None:
            raise DatabaseUnavailableError(
                "The database plugin has not started"
            )
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Open one session and close it without an implicit commit."""
        async with self.sessionmaker() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Open one session inside a transaction.

        The transaction commits when the scope exits normally and rolls
        back on exception or cancellation.
        """
        async with self.sessionmaker() as session:
            async with session.begin():
                yield session


_database: Database | None = None


def get_database() -> Database:
    """Return the started database owner."""
    if _database is None or not _database.started:
        raise DatabaseUnavailableError("The database plugin has not started")
    return _database


def get_engine() -> AsyncEngine:
    """Return the shared engine without creating a new pool."""
    return get_database().engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the factory for per-scope sessions."""
    return get_database().sessionmaker


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """Open a session that closes without committing business writes."""
    async with get_database().session() as value:
        yield value


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    """Open a session in a transaction that commits or rolls back."""
    async with get_database().transaction() as value:
        yield value


@on_startup
async def _startup() -> None:
    global _database
    if _database is not None:
        if _database.started:
            return
        raise DatabaseUnavailableError("The database has unfinished cleanup")
    owner = Database(get_plugin_config(Settings))
    try:
        await owner.startup()
    except BaseException:
        if owner._engine is not None:
            _database = owner
        raise
    _database = owner


@on_shutdown
async def _shutdown() -> None:
    global _database
    if _database is not None:
        await _database.shutdown()
        _database = None
