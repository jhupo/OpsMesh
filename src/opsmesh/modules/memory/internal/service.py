"""Durable two-stage memory jobs with immutable candidates and CAS publication."""

import hashlib
import json
import time
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryConsolidator,
    MemoryExtractor,
    MemoryJob,
    MemoryNamespace,
    MemoryNamespaceSettings,
    MemoryRetentionResult,
    MemorySource,
    MemoryUsageObserver,
)
from opsmesh.modules.memory.events import MemoryRevisionPublished
from opsmesh.modules.workspaces.api import WorkspaceService

from .generation import MemoryGenerationWorker
from .requests import create_memory_job
from .retention import prune_memory


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class PersistentMemoryService:
    def __init__(
        self,
        database: AsyncDatabase,
        workspaces: WorkspaceService,
        extractor: MemoryExtractor,
        consolidator: MemoryConsolidator,
        *,
        observe_usage: MemoryUsageObserver | None = None,
    ) -> None:
        self._namespaces = VersionedRecordStore(database, "memory_namespaces")
        self._sources = VersionedRecordStore(database, "memory_sources")
        self._jobs = VersionedRecordStore(database, "memory_jobs")
        self._extractions = VersionedRecordStore(database, "memory_extractions")
        self._candidates = VersionedRecordStore(database, "memory_candidates")
        self._workspaces = workspaces
        self._worker = MemoryGenerationWorker(
            namespaces=self._namespaces,
            sources=self._sources,
            jobs=self._jobs,
            extractions=self._extractions,
            candidates=self._candidates,
            workspaces=workspaces,
            extractor=extractor,
            consolidator=consolidator,
            namespace_reader=self._namespace,
            candidate_reader=self._candidate,
            observer=observe_usage,
        )

    async def create_namespace(self, namespace: MemoryNamespace) -> MemoryNamespace:
        if (
            not namespace.id
            or not namespace.owner
            or namespace.memory_revision
            or namespace.artifact_digest
            or namespace.last_job_id
            or namespace.deleted
            or namespace.revision != 1
        ):
            raise OpsMeshError("CONFIG_INVALID", "Invalid initial memory namespace")
        await self._namespaces.create(namespace.id, namespace.model_dump(mode="json"))
        return namespace

    async def _namespace(self, namespace_id: str) -> MemoryNamespace:
        record = await self._namespaces.get(namespace_id)
        if record is None or record.data.get("deleted"):
            raise OpsMeshError("NOT_FOUND", "Memory namespace not found")
        return MemoryNamespace.model_validate({**record.data, "revision": record.revision})

    @staticmethod
    def _authorize(namespace: MemoryNamespace, principal_id: str, *, write: bool = False) -> None:
        allowed = namespace.contributors if write else namespace.readers + namespace.contributors
        if principal_id != namespace.owner and principal_id not in allowed:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Memory namespace access denied")

    async def get_namespace(self, namespace_id: str, *, principal_id: str) -> MemoryNamespace:
        namespace = await self._namespace(namespace_id)
        self._authorize(namespace, principal_id)
        return namespace

    async def list_namespaces(self, *, principal_id: str) -> tuple[MemoryNamespace, ...]:
        values = []
        for row in await self._namespaces.list():
            if row.data.get("deleted"):
                continue
            namespace = MemoryNamespace.model_validate({**row.data, "revision": row.revision})
            if principal_id in {namespace.owner, *namespace.readers, *namespace.contributors}:
                values.append(namespace)
        return tuple(values)

    async def configure_namespace(
        self,
        namespace_id: str,
        settings: MemoryNamespaceSettings,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> MemoryNamespace:
        namespace = await self._namespace(namespace_id)
        if principal_id != namespace.owner:
            raise OpsMeshError(
                "AUTHORIZATION_DENIED", "Only namespace owners may change access or policy"
            )
        data = {**namespace.model_dump(mode="json"), **settings.model_dump(mode="json")}
        row = await self._namespaces.update(namespace_id, data, expected_revision)
        return MemoryNamespace.model_validate({**row.data, "revision": row.revision})

    async def append_source(self, source: MemorySource, *, principal_id: str) -> MemorySource:
        namespace = await self._namespace(source.namespace_id)
        self._authorize(namespace, principal_id, write=True)
        if not namespace.policy.generate:
            raise OpsMeshError(
                "AUTHORIZATION_DENIED", "Memory namespace does not accept generation sources"
            )
        if not source.id or not source.conversation_id or not source.run_id:
            raise OpsMeshError(
                "CONFIG_INVALID", "Source, conversation and run identifiers are required"
            )
        source = source.model_copy(
            update={
                "principal_id": principal_id,
                "extract_model_profile": namespace.policy.extract_model_profile,
                "consolidate_model_profile": namespace.policy.consolidate_model_profile,
            }
        )
        key = digest([source.namespace_id, source.id])
        data = source.model_dump(mode="json", exclude={"text"})
        data["text_digest"] = await self._workspaces.put_blob(source.text.encode())
        prior = await self._sources.get(key)
        if prior is not None:
            if prior.data != data:
                raise OpsMeshError("CONFLICT", "Memory source id has different content")
            return source
        await self._sources.create(key, data)
        return source

    async def _candidate(self, namespace: MemoryNamespace) -> MemoryCandidate:
        if namespace.artifact_digest is None:
            return MemoryCandidate(summary="", index="")
        return MemoryCandidate.model_validate_json(
            await self._workspaces.get_blob(namespace.artifact_digest)
        )

    async def read(self, namespace_id: str, *, principal_id: str) -> MemoryCandidate:
        namespace = await self._namespace(namespace_id)
        self._authorize(namespace, principal_id)
        if not namespace.policy.read:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Memory reading is disabled")
        return await self._candidate(namespace)

    async def request_generation(
        self, namespace_id: str, *, principal_id: str, idempotency_key: str
    ) -> MemoryJob:
        namespace = await self._namespace(namespace_id)
        self._authorize(namespace, principal_id, write=True)
        if not namespace.policy.generate:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Memory generation is disabled")
        if not idempotency_key:
            raise OpsMeshError("CONFIG_INVALID", "Generation requires an idempotency key")
        job_id = digest([namespace_id, idempotency_key])
        return await create_memory_job(
            namespace,
            principal_id=principal_id,
            job_id=job_id,
            namespaces=self._namespaces,
            sources=self._sources,
            jobs=self._jobs,
        )

    async def run_job(self, job_id: str) -> MemoryJob:
        return await self._worker.run(job_id)

    async def get_job(self, job_id: str, *, principal_id: str) -> MemoryJob:
        record = await self._jobs.get(job_id)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Memory job not found")
        job = MemoryJob.model_validate(record.data["job"])
        await self.get_namespace(job.namespace_id, principal_id=principal_id)
        return job

    async def list_jobs(self, namespace_id: str, *, principal_id: str) -> tuple[MemoryJob, ...]:
        await self.get_namespace(namespace_id, principal_id=principal_id)
        return tuple(
            MemoryJob.model_validate(record.data["job"])
            for record in await self._jobs.list()
            if record.data["job"]["namespace_id"] == namespace_id
        )

    async def list_pending_jobs(self) -> tuple[MemoryJob, ...]:
        jobs: list[MemoryJob] = []
        for record in await self._jobs.list():
            job = MemoryJob.model_validate(record.data["job"])
            if job.status in {"succeeded", "no_change", "failed"}:
                continue
            if record.data.get("lease_until", 0) > time.time():
                continue
            try:
                await self._namespace(job.namespace_id)
            except OpsMeshError as error:
                if error.code == "NOT_FOUND":
                    try:
                        await self._worker.run(job.id)
                    except OpsMeshError as conflict:
                        if conflict.code != "CONFLICT":
                            raise
                    continue
                raise
            jobs.append(job)
        return tuple(jobs)

    async def forget(self, namespace_id: str, *, principal_id: str) -> None:
        namespace = await self._namespace(namespace_id)
        if principal_id != namespace.owner:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Only the namespace owner may delete it")
        # Tombstone before cleanup so concurrent publishers fail CAS and readers lose access.
        deleted = namespace.model_copy(update={"deleted": True, "artifact_digest": None})
        await self._namespaces.update(
            namespace_id, deleted.model_dump(mode="json"), expected_revision=namespace.revision
        )
        for source in await self._sources.list():
            if source.data["namespace_id"] == namespace_id:
                await self._sources.delete(source.id, expected_revision=source.revision)
        for store in (self._extractions, self._candidates):
            for record in await store.list():
                if record.data["namespace_id"] == namespace_id:
                    await store.delete(record.id, expected_revision=record.revision)
        # Content-addressed blobs may be shared. Physical collection requires a retention/GC pass.

    async def update(
        self,
        namespace_id: str,
        candidate: MemoryCandidate,
        *,
        principal_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> MemoryNamespace:
        namespace = await self._namespace(namespace_id)
        self._authorize(namespace, principal_id, write=True)
        if not namespace.policy.live_update:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Live memory updates are disabled")
        if not idempotency_key or not candidate.summary.strip() or not candidate.index.strip():
            raise OpsMeshError(
                "CONFIG_INVALID", "Live memory requires content and an idempotency key"
            )
        clean = candidate.model_copy(update={"usage": {}})
        content_digest = await self._workspaces.put_blob(clean.model_dump_json().encode())
        key = digest([principal_id, idempotency_key])
        prior = namespace.live_updates.get(key)
        if prior is not None:
            if prior != content_digest:
                raise OpsMeshError("CONFLICT", "Live memory update key has different content")
            return namespace
        if namespace.revision != expected_revision:
            raise OpsMeshError("CONFLICT", "Memory revision changed")
        updated = namespace.model_copy(
            update={
                "artifact_digest": content_digest,
                "memory_revision": namespace.memory_revision + 1,
                "live_updates": {**namespace.live_updates, key: content_digest},
                "last_job_id": None,
            }
        )
        event = MemoryRevisionPublished(
            payload={
                "namespace_id": namespace_id,
                "memory_revision": updated.memory_revision,
                "artifact_digest": content_digest,
                "actor_ref": principal_id,
                "reason": "live_update",
            }
        )
        record = await self._namespaces.update(
            namespace_id,
            updated.model_dump(mode="json"),
            expected_revision=expected_revision,
            events=(event.model_dump(mode="json"),),
        )
        return updated.model_copy(update={"revision": record.revision})

    async def prune(self, namespace_id: str, *, principal_id: str) -> MemoryRetentionResult:
        namespace = await self._namespace(namespace_id)
        if namespace.owner != principal_id:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Only the namespace owner may prune memory")
        return await prune_memory(
            namespace,
            namespaces=self._namespaces,
            sources=self._sources,
            jobs=self._jobs,
            extractions=self._extractions,
            candidates=self._candidates,
        )
