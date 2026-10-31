"""Commit delivery outcomes independently of concurrent inbox read-state changes."""

from datetime import UTC, datetime

from sqlalchemy import select, update

from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.database.records import validated_json
from opsmesh.infrastructure.database.transactions import serialized_write


class NotificationDeliveryState:
    def __init__(self, records: VersionedRecordStore) -> None:
        self.records = records

    async def complete(self, claim: StoredRecord, outcome: JsonData) -> bool:
        """Merge a confirmed result only while the same delivery attempt owns the row.

        An expired lease may already be marked unknown. A late receipt resolves that
        uncertainty without sending again. An outcome from an older attempt cannot
        overwrite a retried delivery. Reading the inbox does not transfer ownership.
        """
        table = self.records.table
        self.records.database.require_table(table.name)
        async with serialized_write(self.records.database.engine) as connection:
            row = (
                (
                    await connection.execute(
                        select(table).where(table.c.id == claim.id).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            data = row["data"]
            if data.get("attempts") != claim.data["attempts"] or data.get("status") not in {
                "delivering",
                "unknown",
            }:
                return False
            await connection.execute(
                update(table)
                .where(table.c.id == claim.id, table.c.revision == row["revision"])
                .values(
                    data=validated_json(
                        {**data, "error_code": None, **outcome, "lease_until": None}
                    ),
                    revision=row["revision"] + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return True
