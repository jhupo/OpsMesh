"""Versioned schema installation, declared upgrades and strict drift detection."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import CheckConstraint

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database.transactions import database_connection, serialized_write

SCHEMA_VERSION = 1
SCHEMA_TABLE = "opsmesh_schema_versions"


def schema_versions_table(metadata: MetaData) -> Table:
    return Table(
        SCHEMA_TABLE,
        metadata,
        Column("table_name", String(128), primary_key=True),
        Column("version", Integer, nullable=False),
        Column("fingerprint", String(64), nullable=False),
        Column("applied_at", DateTime(timezone=True), nullable=False),
    )


def _type_shape(column_type: Any) -> tuple[str, int | None]:
    name = column_type.__class__.__name__.lower()
    if any(part in name for part in ("char", "string")):
        return "string", column_type.length
    if "integer" in name or name in {"int", "bigint", "smallint"}:
        return "integer", None
    if "datetime" in name or "timestamp" in name:
        return "datetime", None
    if "json" in name:
        return "json", None
    if name in {"float", "double", "double_precision", "real"}:
        return "float", None
    return name, None


def _normalized_sql(value: str) -> str:
    return " ".join(value.lower().replace('"', "").split())


def declared_shape(table: Table) -> dict[str, Any]:
    return {
        "columns": sorted(
            (column.name, _type_shape(column.type), bool(column.nullable))
            for column in table.columns
        ),
        "primary_key": sorted(column.name for column in table.primary_key.columns),
        "indexes": sorted(
            (index.name, tuple(column.name for column in index.columns), bool(index.unique))
            for index in table.indexes
        ),
        "checks": sorted(
            _normalized_sql(str(constraint.sqltext))
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        ),
    }


def schema_fingerprint(table: Table) -> str:
    return hashlib.sha256(json.dumps(declared_shape(table), sort_keys=True).encode()).hexdigest()


def inspect_shape(connection: Connection, table_name: str) -> dict[str, Any]:
    inspector = inspect(connection)
    return {
        "columns": sorted(
            (column["name"], _type_shape(column["type"]), bool(column["nullable"]))
            for column in inspector.get_columns(table_name)
        ),
        "primary_key": sorted(inspector.get_pk_constraint(table_name)["constrained_columns"]),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table_name)
        ),
        "checks": sorted(
            _normalized_sql(constraint["sqltext"])
            for constraint in inspector.get_check_constraints(table_name)
        ),
    }


def validate_table(connection: Connection, table: Table, applied: dict[str, Any]) -> None:
    if applied["version"] != table.info.get("schema_version", SCHEMA_VERSION) or applied[
        "fingerprint"
    ] != schema_fingerprint(table):
        raise OpsMeshError(
            "SCHEMA_VERSION_MISMATCH", f"Schema migration is required for {table.name}"
        )
    if inspect_shape(connection, table.name) != declared_shape(table):
        raise OpsMeshError("SCHEMA_DRIFT", f"Database structure differs for {table.name}")


def validate_version_catalog(connection: Connection, versions: Table) -> None:
    if inspect_shape(connection, versions.name) != declared_shape(versions):
        raise OpsMeshError("SCHEMA_DRIFT", "The schema migration catalog structure has changed")


async def migrate_schema(engine: AsyncEngine, metadata: MetaData, versions: Table) -> set[str]:
    """Apply declared migrations; reject unversioned tables and unrelated schema drift."""
    async with serialized_write(engine) as connection:
        if engine.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(784562102931)"))
        existing = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        if SCHEMA_TABLE not in existing:
            if existing.intersection(metadata.tables):
                raise OpsMeshError(
                    "SCHEMA_UNVERSIONED",
                    "Existing application tables require an explicit legacy migration",
                )
            await connection.run_sync(lambda sync: versions.create(sync))
        else:
            await connection.run_sync(validate_version_catalog, versions)
        rows = (await connection.execute(select(versions))).mappings().all()
        applied = {row["table_name"]: dict(row) for row in rows}
        for table in metadata.sorted_tables:
            if table.name in applied:
                if table.name not in existing and table.name != SCHEMA_TABLE:
                    raise OpsMeshError(
                        "SCHEMA_DRIFT", f"Previously migrated table {table.name} is missing"
                    )
                if applied[table.name]["version"] < table.info.get(
                    "schema_version", SCHEMA_VERSION
                ):
                    from .projections import migrate_record_projections

                    await connection.run_sync(
                        migrate_record_projections, table, applied[table.name], versions
                    )
                    current = (
                        (
                            await connection.execute(
                                select(versions).where(versions.c.table_name == table.name)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    applied[table.name] = dict(current)
                await connection.run_sync(validate_table, table, applied[table.name])
                continue
            if table.name in existing:
                raise OpsMeshError(
                    "SCHEMA_UNVERSIONED", f"Table {table.name} has no migration record"
                )
            if table.name != SCHEMA_TABLE:
                await connection.run_sync(table.create)
            await connection.execute(
                insert(versions).values(
                    table_name=table.name,
                    version=table.info.get("schema_version", SCHEMA_VERSION),
                    fingerprint=schema_fingerprint(table),
                    applied_at=datetime.now(UTC),
                )
            )
    return set(metadata.tables)


async def verify_schema(engine: AsyncEngine, metadata: MetaData, versions: Table) -> set[str]:
    """Read-only compatibility verification for service startup."""
    async with database_connection(engine) as connection:
        existing = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        if SCHEMA_TABLE not in existing:
            raise OpsMeshError("SCHEMA_NOT_INITIALIZED", "Run the database migration command first")
        await connection.run_sync(validate_version_catalog, versions)
        rows = (await connection.execute(select(versions))).mappings().all()
        applied = {row["table_name"]: dict(row) for row in rows}
        for table in metadata.sorted_tables:
            if table.name not in existing or table.name not in applied:
                raise OpsMeshError(
                    "SCHEMA_NOT_INITIALIZED", f"Table {table.name} has not been migrated"
                )
            await connection.run_sync(validate_table, table, applied[table.name])
    return set(metadata.tables)
