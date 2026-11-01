"""Consistent native database backups with credentials kept out of child argv."""

import asyncio
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from sqlalchemy import inspect
from sqlalchemy.engine import URL, Connection, make_url

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase

DatabaseKind = Literal["sqlite", "postgresql"]


def database_kind(url: str) -> DatabaseKind:
    driver = make_url(url).drivername.split("+", 1)[0]
    if driver == "sqlite":
        return "sqlite"
    if driver in {"postgres", "postgresql"}:
        return "postgresql"
    raise OpsMeshError("FEATURE_UNSUPPORTED", "Backup supports SQLite and PostgreSQL")


def sqlite_path(url: str) -> Path:
    value = make_url(url)
    if value.database in {None, "", ":memory:"} or value.query:
        raise OpsMeshError("FEATURE_UNSUPPORTED", "Backup requires a file-backed SQLite database")
    assert value.database is not None
    return Path(value.database).resolve()


def _postgres_environment(url: URL) -> dict[str, str]:
    if not url.database or "=" in url.database or "://" in url.database:
        raise OpsMeshError("CONFIG_INVALID", "Backup requires a literal PostgreSQL database name")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    for name, value in {
        "PGHOST": url.host,
        "PGPORT": url.port,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }.items():
        if value is not None:
            environment[name] = str(value)
    options = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "channel_binding": "PGCHANNELBINDING",
        "application_name": "PGAPPNAME",
    }
    for key, option in url.query.items():
        if key not in options or not isinstance(option, str):
            raise OpsMeshError("FEATURE_UNSUPPORTED", "Unsupported PostgreSQL backup URL option")
        environment[options[key]] = option
    environment.setdefault("PGCONNECT_TIMEOUT", "15")
    # A deliberately supplied pgpass file is supported without putting its content in argv.
    if "PGPASSFILE" in os.environ:
        environment["PGPASSFILE"] = os.environ["PGPASSFILE"]
    return environment


async def _postgres_command(url: str, arguments: list[str], *, timeout_seconds: float) -> None:
    environment = _postgres_environment(make_url(url))
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as error:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE", "PostgreSQL client tools are required"
        ) from error
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except (TimeoutError, asyncio.CancelledError) as error:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(error, TimeoutError):
            raise OpsMeshError("BACKUP_TIMEOUT", "Database backup/restore timed out") from error
        raise
    if process.returncode != 0:
        raise OpsMeshError(
            "DATABASE_BACKUP_FAILED",
            "PostgreSQL backup/restore command failed",
            details={"exit_code": process.returncode},
        )


def _sqlite_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        raise OpsMeshError("CONFLICT", "Database destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(destination)) as target,
    ):
        original.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise OpsMeshError("CONTENT_CORRUPT", "SQLite integrity validation failed")


async def dump_database(url: str, destination: Path, *, timeout_seconds: float = 600) -> None:
    if database_kind(url) == "sqlite":
        await asyncio.to_thread(_sqlite_copy, sqlite_path(url), destination)
        return
    if destination.exists():
        raise OpsMeshError("CONFLICT", "Database backup file already exists")
    await _postgres_command(
        url,
        ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(destination)],
        timeout_seconds=timeout_seconds,
    )


async def require_empty_database(url: str) -> None:
    if database_kind(url) == "sqlite":
        if sqlite_path(url).exists():
            raise OpsMeshError("RESTORE_TARGET_NOT_EMPTY", "SQLite target file already exists")
        return
    database = AsyncDatabase(url)
    try:
        async with database.engine.connect() as connection:

            def populated(sync: Connection) -> bool:
                inspector = inspect(sync)
                for schema in inspector.get_schema_names():
                    if schema == "information_schema" or schema.startswith("pg_"):
                        continue
                    if (
                        inspector.get_table_names(schema=schema)
                        or inspector.get_view_names(schema=schema)
                        or inspector.get_materialized_view_names(schema=schema)
                        or inspector.get_sequence_names(schema=schema)
                    ):
                        return True
                return False

            if await connection.run_sync(populated):
                raise OpsMeshError("RESTORE_TARGET_NOT_EMPTY", "PostgreSQL target is not empty")
    finally:
        await database.close()


async def restore_database(url: str, source: Path, *, timeout_seconds: float = 600) -> None:
    await require_empty_database(url)
    if database_kind(url) == "sqlite":
        await asyncio.to_thread(_sqlite_copy, source, sqlite_path(url))
        return
    name = make_url(url).database
    assert name is not None
    await _postgres_command(
        url,
        [
            "pg_restore",
            "--single-transaction",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            name,
            str(source),
        ],
        timeout_seconds=timeout_seconds,
    )
