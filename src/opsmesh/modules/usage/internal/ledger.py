"""One CAS record owns each principal's costs and reservations atomically."""

import asyncio
import copy
from collections.abc import Callable
from typing import Any, TypeVar

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore

T = TypeVar("T")


def empty_ledger() -> dict[str, Any]:
    return {"observations": {}, "runs": {}, "limits": {}, "spent": {}, "reservations": {}}


class PrincipalUsageLedger:
    def __init__(self, database: AsyncDatabase) -> None:
        self._records = VersionedRecordStore(database, "usage_ledgers")

    async def read(self, principal_id: str) -> dict[str, Any]:
        record = await self._records.get(principal_id)
        return copy.deepcopy(record.data) if record is not None else empty_ledger()

    async def mutate(self, principal_id: str, operation: Callable[[dict[str, Any]], T]) -> T:
        if not principal_id:
            raise OpsMeshError("CONFIG_INVALID", "A principal is required")
        for _ in range(32):
            record = await self._records.get(principal_id)
            data = copy.deepcopy(record.data) if record is not None else empty_ledger()
            result = operation(data)
            try:
                if record is None:
                    await self._records.create(principal_id, data)
                else:
                    await self._records.update(
                        principal_id, data, expected_revision=record.revision
                    )
                return result
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
                await asyncio.sleep(0)
        raise OpsMeshError(
            "CONCURRENCY_LIMIT", "Usage ledger is busy; retry the operation", retryable=True
        )
