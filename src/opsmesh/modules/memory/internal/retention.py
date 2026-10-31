"""Retention and job-input selection share a database namespace row lock."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.infrastructure.database.transactions import serialized_write
from opsmesh.modules.memory.api import MemoryNamespace, MemoryRetentionResult


def expired(value: datetime, now: datetime, ttl: float | None) -> bool:
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return ttl is not None and value < now - timedelta(seconds=ttl)


async def prune_memory(
    namespace: MemoryNamespace,
    *,
    namespaces: VersionedRecordStore,
    sources: VersionedRecordStore,
    jobs: VersionedRecordStore,
    extractions: VersionedRecordStore,
    candidates: VersionedRecordStore,
) -> MemoryRetentionResult:
    now = datetime.now(UTC)
    job_deleted = source_deleted = stage_deleted = 0
    retained, protected_sources = set(), set()
    async with serialized_write(jobs.database.engine) as connection:
        current = (
            (
                await connection.execute(
                    select(namespaces.table)
                    .where(namespaces.table.c.id == namespace.id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if current["revision"] != namespace.revision or current["data"].get("deleted"):
            raise OpsMeshError("CONFLICT", "Memory namespace changed")
        records = (
            await connection.execute(
                select(jobs.table)
                .where(jobs.table.c.data["job"]["namespace_id"].as_string() == namespace.id)
                .with_for_update()
            )
        ).mappings()
        for record in records:
            job = record["data"]["job"]
            if job["namespace_id"] != namespace.id:
                continue
            if (
                expired(record["updated_at"], now, namespace.policy.job_retention_seconds)
                and job["status"] in {"succeeded", "no_change", "failed"}
                and record["data"].get("lease_until", 0) <= now.timestamp()
            ):
                await connection.execute(delete(jobs.table).where(jobs.table.c.id == record["id"]))
                job_deleted += 1
            else:
                retained.add(record["id"])
                protected_sources.update(job["source_ids"])
        records = (await connection.execute(select(sources.table))).mappings()
        for record in records:
            if (
                record["data"]["namespace_id"] == namespace.id
                and record["id"] not in protected_sources
                and expired(record["updated_at"], now, namespace.policy.source_retention_seconds)
            ):
                result = await connection.execute(
                    delete(sources.table).where(
                        sources.table.c.id == record["id"],
                        sources.table.c.revision == record["revision"],
                    )
                )
                source_deleted += result.rowcount
        for store in (extractions, candidates):
            records = (await connection.execute(select(store.table))).mappings()
            for record in records:
                job_id = record["data"].get("job_id")
                if (
                    record["data"]["namespace_id"] == namespace.id
                    and job_id
                    and job_id not in retained
                ):
                    result = await connection.execute(
                        delete(store.table).where(
                            store.table.c.id == record["id"],
                            store.table.c.revision == record["revision"],
                        )
                    )
                    stage_deleted += result.rowcount
    return MemoryRetentionResult(
        sources_deleted=source_deleted,
        jobs_deleted=job_deleted,
        stage_results_deleted=stage_deleted,
    )
