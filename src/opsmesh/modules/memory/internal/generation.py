"""Two-stage generation worker; candidates remain private until namespace CAS publication."""

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryConsolidator,
    MemoryExtraction,
    MemoryExtractor,
    MemoryJob,
    MemoryNamespace,
    MemorySource,
    MemoryUsageObserver,
)
from opsmesh.modules.memory.events import MemoryRevisionPublished
from opsmesh.modules.workspaces.api import WorkspaceService


class MemoryGenerationWorker:
    def __init__(
        self,
        *,
        namespaces: VersionedRecordStore,
        sources: VersionedRecordStore,
        jobs: VersionedRecordStore,
        extractions: VersionedRecordStore,
        candidates: VersionedRecordStore,
        workspaces: WorkspaceService,
        extractor: MemoryExtractor,
        consolidator: MemoryConsolidator,
        namespace_reader: Callable[[str], Awaitable[MemoryNamespace]],
        candidate_reader: Callable[[MemoryNamespace], Awaitable[MemoryCandidate]],
        observer: MemoryUsageObserver | None,
    ) -> None:
        self._namespaces, self._sources, self._jobs = namespaces, sources, jobs
        self._extractions, self._candidates = extractions, candidates
        self._workspaces, self._extract, self._consolidate = workspaces, extractor, consolidator
        self._namespace, self._candidate, self._observer = (
            namespace_reader,
            candidate_reader,
            observer,
        )

    async def run(self, job_id: str) -> MemoryJob:
        record = await self._jobs.get(job_id)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Memory job not found")
        job = MemoryJob.model_validate(record.data["job"])
        if job.status in {"succeeded", "no_change"} or (
            job.status == "failed"
            and job.error_code in {"MEMORY_NAMESPACE_CHANGED", "MEMORY_NAMESPACE_UNAVAILABLE"}
        ):
            return job
        if record.data.get("lease_until", 0) > time.time():
            raise OpsMeshError("CONFLICT", "Memory job is already running")
        try:
            namespace = await self._namespace(job.namespace_id)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            return await self._invalidate(record, job, "MEMORY_NAMESPACE_UNAVAILABLE")
        if namespace.last_job_id == job.id and namespace.artifact_digest == job.artifact_digest:
            return await self._finish(record, job.model_copy(update={"status": "succeeded"}))
        if namespace.revision != job.base_revision:
            return await self._invalidate(record, job, "MEMORY_NAMESPACE_CHANGED")
        job = job.model_copy(update={"status": "extracting", "error_code": None})
        record = await self._claim(record, job, namespace)
        try:
            async with asyncio.timeout(namespace.policy.timeout_seconds):
                items = await self._extract_sources(job, int(record.data["attempt"]))
                if not items:
                    return await self._finish(
                        record, job.model_copy(update={"status": "no_change"})
                    )
                job = job.model_copy(update={"status": "consolidating"})
                record = await self._save(record, job)
                candidate = await self._consolidate_sources(
                    job, namespace, items, int(record.data["attempt"])
                )
                candidate_digest = await self._workspaces.put_blob(
                    candidate.model_copy(update={"usage": {}}).model_dump_json().encode()
                )
                job = job.model_copy(
                    update={"status": "publishing", "artifact_digest": candidate_digest}
                )
                record = await self._save(record, job)
                await self._publish(namespace, job)
        except BaseException as error:
            code = (
                error.code
                if isinstance(error, OpsMeshError)
                else (
                    "MEMORY_TIMEOUT"
                    if isinstance(error, TimeoutError)
                    else "MEMORY_GENERATION_FAILED"
                )
            )
            await self._finish(
                record, job.model_copy(update={"status": "failed", "error_code": code})
            )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise OpsMeshError(
                code, "Memory generation failed; published memory is retained"
            ) from error
        return await self._finish(record, job.model_copy(update={"status": "succeeded"}))

    async def _invalidate(self, record: StoredRecord, job: MemoryJob, code: str) -> MemoryJob:
        """Close obsolete inputs with CAS; never overwrite a newly claimed attempt."""
        invalid = job.model_copy(update={"status": "failed", "error_code": code})
        try:
            return await self._finish(record, invalid)
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            current = await self._jobs.get(record.id)
            if current is not None:
                latest = MemoryJob.model_validate(current.data["job"])
                if latest.status == "failed" and latest.error_code == code:
                    return latest
            raise

    async def _claim(
        self, record: StoredRecord, job: MemoryJob, namespace: MemoryNamespace
    ) -> StoredRecord:
        return await self._jobs.update(
            record.id,
            {
                **record.data,
                "job": job.model_dump(mode="json"),
                "lease_until": time.time() + namespace.policy.timeout_seconds + 30,
                "attempt": int(record.data["attempt"]) + 1,
            },
            expected_revision=record.revision,
        )

    async def _save(self, record: StoredRecord, job: MemoryJob) -> StoredRecord:
        return await self._jobs.update(
            record.id,
            {**record.data, "job": job.model_dump(mode="json")},
            expected_revision=record.revision,
        )

    async def _finish(self, record: StoredRecord, job: MemoryJob) -> MemoryJob:
        await self._jobs.update(
            record.id,
            {
                **record.data,
                "job": job.model_dump(mode="json"),
                "lease_until": 0.0,
            },
            expected_revision=record.revision,
        )
        return job

    async def _extract_sources(self, job: MemoryJob, attempt: int) -> tuple[MemoryExtraction, ...]:
        items: list[MemoryExtraction] = []
        for source_key in job.source_ids:
            record = await self._sources.get(source_key)
            if record is None:
                raise OpsMeshError("MEMORY_SOURCE_UNAVAILABLE", "Memory source was removed")
            content = await self._workspaces.get_blob(record.data["text_digest"])
            source = MemorySource.model_validate(
                {
                    **{key: value for key, value in record.data.items() if key != "text_digest"},
                    "text": content.decode(),
                }
            )
            key = hashlib.sha256(
                f"{job.id}:{source_key}:{record.data['text_digest']}".encode()
            ).hexdigest()
            saved = await self._extractions.get(key)
            if saved is None:
                extraction = await self._extract(source)
                self._validate_extraction(source, extraction)
                extraction = extraction.model_copy(
                    update={
                        "principal_id": source.principal_id,
                        "model_profile": source.extract_model_profile,
                        "consolidate_model_profile": source.consolidate_model_profile,
                    }
                )
                saved = await self._extractions.create(
                    key,
                    {
                        "namespace_id": job.namespace_id,
                        "job_id": job.id,
                        "extraction": extraction.model_dump(mode="json"),
                    },
                )
            extraction = MemoryExtraction.model_validate(saved.data["extraction"])
            if self._observer is not None:
                await self._observer(
                    job.id,
                    "extract",
                    {
                        "usage_id": key,
                        "attempt": attempt,
                        "usage": extraction.usage,
                        "available": bool(extraction.usage),
                    },
                )
            if extraction.notes.strip():
                items.append(extraction)
        return tuple(items)

    @staticmethod
    def _validate_extraction(source: MemorySource, extraction: MemoryExtraction) -> None:
        if extraction.source_id != source.id:
            raise OpsMeshError("MODEL_OUTPUT_INVALID", "Extraction source identity changed")
        if bool(extraction.summary.strip()) != bool(extraction.notes.strip()):
            raise OpsMeshError("MODEL_OUTPUT_INVALID", "Extraction is partially empty")

    async def _consolidate_sources(
        self,
        job: MemoryJob,
        namespace: MemoryNamespace,
        items: tuple[MemoryExtraction, ...],
        attempt: int,
    ) -> MemoryCandidate:
        saved = await self._candidates.get(job.id)
        if saved is None:
            candidate = await self._consolidate(items, await self._candidate(namespace))
            if not candidate.summary.strip() or not candidate.index.strip():
                raise OpsMeshError(
                    "MODEL_OUTPUT_INVALID", "Memory candidate requires summary and index"
                )
            saved = await self._candidates.create(
                job.id,
                {
                    "namespace_id": job.namespace_id,
                    "job_id": job.id,
                    "candidate": candidate.model_dump(mode="json"),
                },
            )
        candidate = MemoryCandidate.model_validate(saved.data["candidate"])
        if self._observer is not None:
            await self._observer(
                job.id,
                "consolidate",
                {
                    "usage_id": f"{job.id}:consolidate",
                    "attempt": attempt,
                    "usage": candidate.usage,
                    "available": bool(candidate.usage),
                },
            )
        return candidate

    async def _publish(self, namespace: MemoryNamespace, job: MemoryJob) -> None:
        updated = namespace.model_copy(
            update={
                "artifact_digest": job.artifact_digest,
                "last_job_id": job.id,
                "memory_revision": namespace.memory_revision + 1,
            }
        )
        event = MemoryRevisionPublished(
            payload={
                "namespace_id": namespace.id,
                "memory_revision": updated.memory_revision,
                "job_id": job.id,
                "artifact_digest": job.artifact_digest,
            }
        )
        await self._namespaces.update(
            namespace.id,
            updated.model_dump(mode="json"),
            expected_revision=job.base_revision,
            events=(event.model_dump(mode="json"),),
        )
