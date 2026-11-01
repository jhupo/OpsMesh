"""Create generation input sets atomically with namespace retention decisions."""

from datetime import UTC, datetime

from sqlalchemy import insert, select

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.infrastructure.database.transactions import serialized_write
from opsmesh.modules.memory.api import MemoryJob, MemoryNamespace
from opsmesh.modules.memory.events import MemoryGenerationRequested


async def create_memory_job(
    namespace: MemoryNamespace,
    *,
    principal_id: str,
    job_id: str,
    namespaces: VersionedRecordStore,
    sources: VersionedRecordStore,
    jobs: VersionedRecordStore,
) -> MemoryJob:
    database = jobs.database
    async with serialized_write(database.engine) as connection:
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
        prior = (
            await connection.execute(select(jobs.table.c.data).where(jobs.table.c.id == job_id))
        ).scalar_one_or_none()
        if prior is not None:
            return MemoryJob.model_validate(prior["job"])
        rows = (await connection.execute(select(sources.table))).mappings()
        selected = sorted(
            (row for row in rows if row["data"]["namespace_id"] == namespace.id),
            key=lambda row: (row["updated_at"], row["id"]),
            reverse=True,
        )
        job = MemoryJob(
            id=job_id,
            namespace_id=namespace.id,
            source_ids=tuple(row["id"] for row in selected[: namespace.policy.max_sources]),
            base_revision=namespace.revision,
        )
        data = {
            "job": job.model_dump(mode="json"),
            "policy": namespace.policy.model_dump(mode="json"),
            "base_digest": namespace.artifact_digest,
            "lease_until": 0.0,
            "attempt": 0,
        }
        now = datetime.now(UTC)
        await connection.execute(
            insert(jobs.table).values(
                id=job_id, revision=1, data=data, created_at=now, updated_at=now
            )
        )
        event = MemoryGenerationRequested(
            actor_ref=principal_id, payload={"job_id": job.id, "namespace_id": namespace.id}
        )
        await database.enqueue_events(connection, (event.model_dump(mode="json"),))
        return job
