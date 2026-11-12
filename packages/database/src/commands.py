"""Deployment commands owned by the database plugin."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text

from opsmesh.plugins.config import get_plugin_config

from .config import Settings
from .connections import Database
from .migrations import run_migrations, validate_migrations


@asynccontextmanager
async def _connection() -> AsyncIterator[Database]:
    database = Database(get_plugin_config(Settings))
    await database.startup()
    try:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        yield database
    finally:
        await database.shutdown()


async def check() -> None:
    """Check registered migration graphs and PostgreSQL connectivity."""
    validate_migrations()
    async with _connection():
        pass


async def migrate() -> None:
    """Apply registered plugin migrations without starting the HTTP server."""
    async with _connection() as database:
        await run_migrations(database)


async def convert_shared_history() -> None:
    """Convert the explicit OPSMESH_DATABASE_HISTORY_MANIFEST inventory once."""
    from .history import convert_shared_history as convert

    value = os.environ.get("OPSMESH_DATABASE_HISTORY_MANIFEST")
    if not value:
        raise ValueError(
            "OPSMESH_DATABASE_HISTORY_MANIFEST must name a manifest file"
        )
    async with _connection() as database:
        await convert(database, Path(value))
