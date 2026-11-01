"""Module-owned JSON records with optimistic concurrency and atomic event writes."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    delete,
    insert,
    select,
)
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData, OpsMeshContract
from opsmesh.infrastructure.database.transactions import database_connection, serialized_write

if TYPE_CHECKING:
    from opsmesh.infrastructure.database import AsyncDatabase


class StoredRecord(OpsMeshContract):
    id: str
    revision: int
    data: JsonData
    created_at: datetime
    updated_at: datetime


def validated_json(data: JsonData) -> JsonData:
    if not isinstance(data, dict):
        raise OpsMeshError("RECORD_INVALID", "Record data must be a JSON object")
    try:
        _check_json_keys(data)
        payload = json.dumps(data, allow_nan=False)
        decoded: JsonData = json.loads(payload)
        return decoded
    except (TypeError, ValueError, RecursionError) as error:
        raise OpsMeshError(
            "RECORD_INVALID", "Record data must contain portable JSON values"
        ) from error


def _check_json_keys(value: Any) -> None:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for nested in value.values():
            _check_json_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _check_json_keys(nested)


def validate_record_id(record_id: str) -> None:
    if not record_id or len(record_id) > 255 or any(ord(character) < 32 for character in record_id):
        raise OpsMeshError("RECORD_INVALID", "Record id must contain 1 to 255 printable characters")


class VersionedRecordStore:
    def __init__(self, database: AsyncDatabase, table_name: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name) or table_name.startswith(
            "opsmesh_"
        ):
            raise ValueError("Module tables must use a non-reserved lowercase identifier")
        self.database = database
        existing = database.metadata.tables.get(table_name)
        if existing is not None:
            self.table = existing
            return
        self.table = Table(
            table_name,
            database.metadata,
            Column("id", String(255), primary_key=True),
            Column("revision", Integer, nullable=False),
            Column("data", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            CheckConstraint("revision >= 1", name=f"ck_{table_name}_revision"),
        )
        Index(f"ix_{table_name}_created", self.table.c.created_at, self.table.c.id)

    @staticmethod
    def _record(row: Any) -> StoredRecord:
        values = dict(row)
        for name in ("created_at", "updated_at"):
            if values[name].tzinfo is None:
                values[name] = values[name].replace(tzinfo=UTC)
        return StoredRecord.model_validate(values)

    def _ready(self, record_id: str | None = None) -> None:
        self.database.require_table(self.table.name)
        if record_id is not None:
            validate_record_id(record_id)

    async def get(self, record_id: str) -> StoredRecord | None:
        self._ready(record_id)
        async with database_connection(self.database.engine) as connection:
            row = (
                (await connection.execute(select(self.table).where(self.table.c.id == record_id)))
                .mappings()
                .first()
            )
            return self._record(row) if row is not None else None

    async def list(self, *, limit: int | None = None, offset: int = 0) -> list[StoredRecord]:
        self._ready()
        if offset < 0 or (limit is not None and not 1 <= limit <= 10000):
            raise OpsMeshError("CONFIG_INVALID", "Invalid record pagination")
        query = select(self.table).order_by(self.table.c.created_at, self.table.c.id).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        async with database_connection(self.database.engine) as connection:
            return [self._record(row) for row in (await connection.execute(query)).mappings()]

    async def create(
        self, record_id: str, data: JsonData, *, events: Sequence[JsonData] = ()
    ) -> StoredRecord:
        self._ready(record_id)
        now = datetime.now(UTC)
        record = StoredRecord(
            id=record_id, revision=1, data=validated_json(data), created_at=now, updated_at=now
        )
        try:
            async with serialized_write(self.database.engine) as connection:
                await connection.execute(insert(self.table).values(**record.model_dump()))
                await self.database.enqueue_events(connection, events)
        except IntegrityError as error:
            raise OpsMeshError(
                "CONFLICT", "A record or event with this identifier already exists"
            ) from error
        return record

    async def update(
        self,
        record_id: str,
        data: JsonData,
        expected_revision: int,
        *,
        events: Sequence[JsonData] = (),
    ) -> StoredRecord:
        self._ready(record_id)
        if expected_revision < 1:
            raise OpsMeshError("RECORD_INVALID", "Expected revision must be positive")
        try:
            async with serialized_write(self.database.engine) as connection:
                result = await connection.execute(
                    sql_update(self.table)
                    .where(self.table.c.id == record_id, self.table.c.revision == expected_revision)
                    .values(
                        data=validated_json(data),
                        revision=expected_revision + 1,
                        updated_at=datetime.now(UTC),
                    )
                )
                if result.rowcount != 1:
                    raise OpsMeshError("CONFLICT", "The record was changed or removed")
                await self.database.enqueue_events(connection, events)
                row = (
                    (
                        await connection.execute(
                            select(self.table).where(self.table.c.id == record_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                return self._record(row)
        except IntegrityError as error:
            raise OpsMeshError(
                "CONFLICT", "Record update conflicts with an existing event"
            ) from error

    async def delete(
        self, record_id: str, expected_revision: int, *, events: Sequence[JsonData] = ()
    ) -> None:
        self._ready(record_id)
        if expected_revision < 1:
            raise OpsMeshError("RECORD_INVALID", "Expected revision must be positive")
        try:
            async with serialized_write(self.database.engine) as connection:
                result = await connection.execute(
                    delete(self.table).where(
                        self.table.c.id == record_id, self.table.c.revision == expected_revision
                    )
                )
                if result.rowcount != 1:
                    raise OpsMeshError("CONFLICT", "The record was changed or removed")
                await self.database.enqueue_events(connection, events)
        except IntegrityError as error:
            raise OpsMeshError(
                "CONFLICT", "Record deletion conflicts with an existing event"
            ) from error
