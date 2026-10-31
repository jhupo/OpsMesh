"""Renewable claims prevent concurrent retries of the same ingestion job."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore


class KnowledgeJobClaims:
    def __init__(self, database: AsyncDatabase, *, lease_seconds: float = 60) -> None:
        self._records = VersionedRecordStore(database, "knowledge_job_claims")
        self._lease_seconds = lease_seconds

    @asynccontextmanager
    async def hold(self, job_id: str) -> AsyncIterator[None]:
        owner = uuid4().hex
        prior = await self._records.get(job_id)
        if prior is not None and prior.data["expires_at"] > time.time():
            raise OpsMeshError("CONFLICT", "Knowledge job is already being processed")
        data = {"owner": owner, "expires_at": time.time() + self._lease_seconds}
        if prior is None:
            await self._records.create(job_id, data)
        else:
            await self._records.update(job_id, data, prior.revision)
        current_task = asyncio.current_task()
        assert current_task is not None
        lost = False

        async def heartbeat() -> None:
            nonlocal lost
            while True:
                await asyncio.sleep(self._lease_seconds / 3)
                try:
                    row = await self._records.get(job_id)
                    if row is None or row.data["owner"] != owner:
                        raise OpsMeshError("CONFLICT", "Knowledge job claim was lost")
                    await self._records.update(
                        job_id,
                        {"owner": owner, "expires_at": time.time() + self._lease_seconds},
                        row.revision,
                    )
                except Exception:
                    lost = True
                    current_task.cancel()
                    return

        renewal = asyncio.create_task(heartbeat())
        try:
            yield
        except asyncio.CancelledError as error:
            if lost:
                raise OpsMeshError("CONFLICT", "Knowledge job claim was lost") from error
            raise
        finally:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
            row = await self._records.get(job_id)
            if row is not None and row.data["owner"] == owner:
                with contextlib.suppress(OpsMeshError):
                    await self._records.update(
                        job_id, {"owner": owner, "expires_at": 0}, row.revision
                    )
