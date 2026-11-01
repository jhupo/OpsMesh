"""Explicit v1-to-v2 migration for indexed projections of versioned JSON records."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, Table, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn
from sqlalchemy.sql.elements import ColumnElement

from opsmesh.framework.errors import OpsMeshError


def migrate_record_projections(
    connection: Connection, table: Table, applied: dict[str, Any], versions: Table
) -> None:
    from .migrations import declared_shape, inspect_shape, schema_fingerprint

    projections = table.info.get("record_projections")
    if not projections or applied["version"] != 1:
        raise OpsMeshError("SCHEMA_VERSION_MISMATCH", f"No migration defined for {table.name}")
    previous = table.to_metadata(MetaData())
    for index in tuple(previous.indexes):
        if any(column.name in projections for column in index.columns):
            previous.indexes.remove(index)
    for name in projections:
        previous._columns.remove(previous.c[name])
    if applied["fingerprint"] != schema_fingerprint(previous) or inspect_shape(
        connection, table.name
    ) != declared_shape(previous):
        raise OpsMeshError("SCHEMA_DRIFT", f"Cannot migrate changed table {table.name}")
    quote = connection.dialect.identifier_preparer.quote
    for name in projections:
        column = str(CreateColumn(table.c[name]).compile(dialect=connection.dialect))
        connection.execute(text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {column}"))
    values = {}
    for name, (path, kind) in projections.items():
        field: ColumnElement[Any] = table.c.data
        for part in path:
            field = field[part]
        python_type = kind.python_type
        values[name] = (
            field.as_integer()
            if python_type is int
            else field.as_float()
            if python_type is float
            else field.as_string()
        )
    connection.execute(update(table).values(**values))
    for index in table.indexes:
        if any(column.name in projections for column in index.columns):
            index.create(connection)
    connection.execute(
        update(versions)
        .where(versions.c.table_name == table.name)
        .values(
            version=table.info["schema_version"],
            fingerprint=schema_fingerprint(table),
            applied_at=datetime.now(UTC),
        )
    )
