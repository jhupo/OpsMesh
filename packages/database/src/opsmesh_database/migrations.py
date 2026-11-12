"""Validate and execute independent native Alembic histories."""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from . import schema
from .connections import Database, get_database
from .errors import (
    DatabaseUnavailableError,
    MigrationError,
    MigrationRequiredError,
)
from .schema import MigrationSource

logger = logging.getLogger(__name__)
_ALEMBIC_DIR = Path(__file__).with_name("_alembic")
CUTOVER_TABLE = "opsmesh_migration_cutover"
# Keep the advisory-lock identity stable across distribution moves.
_LOCK_NAMESPACE = b"opsmesh.plugins.database.migrations"


def migration_config(source: MigrationSource) -> Config:
    """Build one source's Alembic configuration and version-table identity."""
    config = Config()
    config.set_main_option("sourceless", "true")
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    config.set_main_option("version_locations", str(source.directory))
    config.set_main_option("path_separator", "os")
    config.attributes["version_table"] = source.version_table
    return config


def revision_graph(source: MigrationSource) -> ScriptDirectory:
    """Reject ambiguous heads and dependencies outside the owning plugin."""
    try:
        script = ScriptDirectory.from_config(migration_config(source))
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            revisions = tuple(script.walk_revisions())
        ids = [item.revision for item in revisions]
        if len(ids) != len(set(ids)):
            raise MigrationError(
                f"Duplicate migration revision for {source.owner!r}"
            )
        if script.get_heads() != [source.head]:
            raise MigrationError(
                f"Plugin {source.owner!r} must declare "
                "its single migration head"
            )
        return script
    except (CommandError, RevisionError, KeyError, UserWarning) as error:
        raise MigrationError(
            f"Invalid independent migration graph for {source.owner!r}: {error}"
        ) from error


def _sources(owner: str | None) -> tuple[MigrationSource, ...]:
    if owner is None:
        return schema.registry.sources()
    return (schema.registry.get(owner),)


def validate_migrations(owner: str | None = None) -> None:
    """Validate declarations without connecting to PostgreSQL."""
    for source in _sources(owner):
        revision_graph(source)


def current_revisions(connection: Connection, table: str) -> frozenset[str]:
    """Read native revision state from a specific version table."""
    context = MigrationContext.configure(
        connection, opts={"version_table": table}
    )
    return frozenset(context.get_current_heads())


def require_cutover(connection: Connection, source: MigrationSource) -> None:
    """Reject an unconverted shared history instead of replaying migrations."""
    legacy = current_revisions(connection, "alembic_version")
    if not legacy:
        return
    if inspect(connection).has_table(CUTOVER_TABLE):
        recorded = connection.scalar(
            text(f"SELECT legacy_heads FROM {CUTOVER_TABLE} WHERE id = 1")
        )
        if recorded is not None and frozenset(json.loads(recorded)) == legacy:
            return
        raise MigrationRequiredError(
            "Shared revision history changed after conversion; stop the old "
            "application and review the database before continuing"
        )
    if legacy:
        raise MigrationRequiredError(
            "Shared alembic_version history exists; run the explicit "
            "database.commands.convert_shared_history command before startup "
            "or upgrades"
        )


def _check_source(connection: Connection, source: MigrationSource) -> None:
    require_cutover(connection, source)
    applied = current_revisions(connection, source.version_table)
    if applied != frozenset({source.head}):
        raise MigrationRequiredError(
            f"Plugin {source.owner!r} requires revision {source.head!r}; "
            f"applied revisions: {sorted(applied)!r}"
        )


async def check_migrations(owner: str | None = None) -> None:
    """Check only the selected plugins' own version tables."""
    validate_migrations(owner)
    sources = _sources(owner)
    if not sources:
        return
    try:
        async with get_database().engine.connect() as connection:
            for source in sources:
                await connection.run_sync(_check_source, source)
    except SQLAlchemyError as error:
        raise DatabaseUnavailableError(
            "Cannot read database migration state"
        ) from error


async def lock_migrations(connection: AsyncConnection, timeout: int) -> None:
    """Serialize migrations and explicit history conversion on PostgreSQL."""
    await connection.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": f"{timeout}ms"},
    )
    digest = hashlib.blake2b(_LOCK_NAMESPACE, digest_size=8).digest()
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(digest, "big", signed=True)},
    )


def upgrade_source(connection: Connection, source: MigrationSource) -> None:
    """Apply one independent history on the supplied transaction."""
    require_cutover(connection, source)
    config = migration_config(source)
    config.attributes["connection"] = connection
    command.upgrade(config, source.head)


async def run_migrations(database: Database | None = None) -> None:
    """Apply registered schemas in dependency load order in one transaction."""
    validate_migrations()
    sources = schema.registry.sources()
    if not sources:
        return
    database = database or get_database()
    try:
        async with database.engine.begin() as connection:
            await lock_migrations(
                connection, database.settings.migration_lock_timeout_ms
            )
            for source in sources:
                await connection.run_sync(upgrade_source, source)
    except (SQLAlchemyError, CommandError, RevisionError) as error:
        raise MigrationError("Database migration failed") from error
    logger.info(
        "Applied plugin migrations: %s", ", ".join(s.owner for s in sources)
    )
