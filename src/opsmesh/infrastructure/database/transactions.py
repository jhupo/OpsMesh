"""Dialect-aware write transactions for migration and outbox claiming."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_sqlite_locks: WeakKeyDictionary[AsyncEngine, asyncio.Lock] = WeakKeyDictionary()


@asynccontextmanager
async def database_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Protect SQLite's shared in-memory connection as well as file transactions."""
    if engine.dialect.name == "sqlite":
        lock = _sqlite_locks.setdefault(engine, asyncio.Lock())
        async with lock, engine.connect() as connection:
            yield connection
    else:
        async with engine.connect() as connection:
            yield connection


@asynccontextmanager
async def serialized_write(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with database_connection(engine) as connection:
        if engine.dialect.name == "sqlite":
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            await connection.begin()
        try:
            yield connection
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()
