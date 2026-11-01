"""Collection CRUD and resumable, idempotent text ingestion without stale vectors."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.capabilities.api import EmbeddingProvider, KnowledgeDocument, ToolAuthorization
from opsmesh.modules.capabilities.knowledge import (
    KnowledgeCollection,
    KnowledgeDocumentRecord,
    KnowledgeImportRequest,
    KnowledgeMaintenanceJob,
)

from .knowledge_jobs import KnowledgeJobClaims
from .serialization import canonical


class KnowledgeMaintenance(ABC):
    @property
    @abstractmethod
    def embeddings(self) -> EmbeddingProvider: ...

    @abstractmethod
    async def _embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...

    @abstractmethod
    async def _store_vector(
        self, document: KnowledgeDocument, vector: tuple[float, ...]
    ) -> None: ...

    @abstractmethod
    def _vector_key(self, document: KnowledgeDocument) -> str: ...

    @abstractmethod
    async def add(self, document: KnowledgeDocument, *, principal_id: str) -> None: ...

    _collections: VersionedRecordStore
    _documents: VersionedRecordStore
    _vectors: VersionedRecordStore
    _jobs: VersionedRecordStore
    _job_claims: KnowledgeJobClaims
    _authorize: ToolAuthorization

    async def _ensure_collection(self, collection: str) -> None:
        row = await self._collections.get(collection)
        if row is not None:
            if row.data.get("deleted"):
                raise OpsMeshError("RESOURCE_DISABLED", "Knowledge collection was deleted")
            return
        try:
            await self._collections.create(
                collection, KnowledgeCollection(id=collection).model_dump()
            )
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            await self._ensure_collection(collection)

    async def create_collection(
        self,
        collection: KnowledgeCollection,
        *,
        principal_id: str,
    ) -> KnowledgeCollection:
        await self._authorize(principal_id, f"knowledge:write:{collection.id}")
        if collection.revision != 1 or collection.deleted:
            raise OpsMeshError("CONFIG_INVALID", "Invalid initial knowledge collection")
        await self._collections.create(collection.id, collection.model_dump())
        return collection

    async def list_collections(self, *, principal_id: str) -> tuple[KnowledgeCollection, ...]:
        # Existing installations stored documents before collection metadata existed.
        legacy = {row.data["collection"] for row in await self._documents.list()}
        for identifier in legacy:
            if await self._collections.get(identifier) is not None:
                continue
            try:
                await self._authorize(principal_id, f"knowledge:read:{identifier}")
            except OpsMeshError as error:
                if error.code not in {"FORBIDDEN", "AUTHORIZATION_DENIED"}:
                    raise
                continue
            await self._ensure_collection(identifier)
        result = []
        for row in await self._collections.list():
            if row.data.get("deleted"):
                continue
            try:
                await self._authorize(principal_id, f"knowledge:read:{row.id}")
            except OpsMeshError as error:
                if error.code not in {"FORBIDDEN", "AUTHORIZATION_DENIED"}:
                    raise
                continue
            result.append(
                KnowledgeCollection.model_validate({**row.data, "revision": row.revision})
            )
        return tuple(result)

    async def get_collection(self, collection: str, *, principal_id: str) -> KnowledgeCollection:
        await self._authorize(principal_id, f"knowledge:read:{collection}")
        row = await self._collections.get(collection)
        if row is None and any(
            item.data["collection"] == collection for item in await self._documents.list()
        ):
            await self._ensure_collection(collection)
            row = await self._collections.get(collection)
        if row is None or row.data.get("deleted"):
            raise OpsMeshError("NOT_FOUND", "Knowledge collection not found")
        return KnowledgeCollection.model_validate({**row.data, "revision": row.revision})

    async def update_collection(
        self,
        collection: KnowledgeCollection,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> KnowledgeCollection:
        await self._authorize(principal_id, f"knowledge:write:{collection.id}")
        await self.get_collection(collection.id, principal_id=principal_id)
        if collection.deleted:
            raise OpsMeshError(
                "CONFIG_INVALID", "Delete collections through their delete operation"
            )
        row = await self._collections.update(
            collection.id, collection.model_dump(), expected_revision
        )
        return KnowledgeCollection.model_validate({**row.data, "revision": row.revision})

    async def delete_collection(
        self,
        collection: str,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> None:
        await self._authorize(principal_id, f"knowledge:write:{collection}")
        current = await self.get_collection(collection, principal_id=principal_id)
        await self._collections.update(
            collection,
            current.model_copy(update={"deleted": True}).model_dump(),
            expected_revision,
        )
        for row in await self._documents.list():
            if row.data["collection"] == collection:
                await self._documents.delete(row.id, row.revision)

    async def list_documents(
        self,
        collection: str,
        *,
        principal_id: str,
    ) -> tuple[KnowledgeDocument, ...]:
        await self.get_collection(collection, principal_id=principal_id)
        return tuple(
            KnowledgeDocument.model_validate(row.data)
            for row in await self._documents.list()
            if row.data["collection"] == collection
        )

    async def get_document(self, document_id: str, *, principal_id: str) -> KnowledgeDocument:
        return (await self.get_document_record(document_id, principal_id=principal_id)).document

    async def get_document_record(
        self,
        document_id: str,
        *,
        principal_id: str,
    ) -> KnowledgeDocumentRecord:
        row = await self._documents.get(document_id)
        if row is None:
            raise OpsMeshError("NOT_FOUND", "Knowledge document not found")
        document = KnowledgeDocument.model_validate(row.data)
        await self.get_collection(document.collection, principal_id=principal_id)
        return KnowledgeDocumentRecord(document=document, revision=row.revision)

    async def update_document(
        self,
        document: KnowledgeDocument,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> None:
        prior = await self.get_document(document.id, principal_id=principal_id)
        await self._authorize(principal_id, f"knowledge:write:{prior.collection}")
        if document.collection != prior.collection:
            raise OpsMeshError("CONFIG_INVALID", "A document cannot change collection")
        vector = (await self._embed((document.title + " " + document.text,)))[0]
        # Publish immutable vectors before CAS; readers always key them by exact document content.
        await self._store_vector(document, vector)
        await self._documents.update(
            document.id, document.model_dump(mode="json"), expected_revision
        )

    async def delete_document(
        self,
        document_id: str,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> None:
        document = await self.get_document(document_id, principal_id=principal_id)
        await self._authorize(principal_id, f"knowledge:write:{document.collection}")
        await self._documents.delete(document_id, expected_revision)

    async def get_maintenance_job(
        self,
        job_id: str,
        *,
        principal_id: str,
    ) -> KnowledgeMaintenanceJob:
        row = await self._jobs.get(job_id)
        if row is None:
            raise OpsMeshError("NOT_FOUND", "Knowledge maintenance job not found")
        await self._authorize(principal_id, f"knowledge:read:{row.data['collection']}")
        return KnowledgeMaintenanceJob.model_validate({**row.data["job"], "revision": row.revision})

    async def import_texts(
        self,
        collection: str,
        request: KnowledgeImportRequest,
        *,
        principal_id: str,
    ) -> KnowledgeMaintenanceJob:
        await self._authorize(principal_id, f"knowledge:write:{collection}")
        await self._ensure_collection(collection)
        if request.overlap >= request.chunk_size:
            raise OpsMeshError("CONFIG_INVALID", "Chunk overlap must be smaller than chunk size")
        fingerprint = hashlib.sha256(canonical(request.model_dump())).hexdigest()
        job_id = hashlib.sha256(canonical([collection, request.idempotency_key])).hexdigest()
        documents = []
        for source in request.sources:
            start, index = 0, 0
            while start < len(source.text):
                end = min(start + request.chunk_size, len(source.text))
                if end < len(source.text):
                    boundary = source.text.rfind("\n", start + request.chunk_size // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
                document_id = hashlib.sha256(
                    canonical([collection, source.id, index, fingerprint])
                ).hexdigest()
                documents.append(
                    KnowledgeDocument(
                        id=document_id,
                        collection=collection,
                        title=source.title,
                        text=source.text[start:end],
                    )
                )
                if end == len(source.text):
                    break
                start = max(start + 1, end - request.overlap)
                index += 1
        return await self._run_maintenance(
            collection, job_id, "import", fingerprint, documents, principal_id=principal_id
        )

    async def rebuild_embeddings(
        self,
        collection: str,
        *,
        principal_id: str,
        idempotency_key: str,
    ) -> KnowledgeMaintenanceJob:
        await self._authorize(principal_id, f"knowledge:write:{collection}")
        documents = await self.list_documents(collection, principal_id=principal_id)
        fingerprint = hashlib.sha256(
            canonical([self.embeddings.id, [d.model_dump() for d in documents]])
        ).hexdigest()
        job_id = hashlib.sha256(canonical([collection, "rebuild", idempotency_key])).hexdigest()
        return await self._run_maintenance(
            collection, job_id, "rebuild", fingerprint, documents, principal_id=principal_id
        )

    async def _run_maintenance(
        self,
        collection: str,
        job_id: str,
        kind: Literal["import", "rebuild"],
        fingerprint: str,
        documents: Sequence[KnowledgeDocument],
        *,
        principal_id: str,
    ) -> KnowledgeMaintenanceJob:
        async with self._job_claims.hold(job_id):
            return await self._execute_maintenance(
                collection,
                job_id,
                kind,
                fingerprint,
                documents,
                principal_id=principal_id,
            )

    async def _execute_maintenance(
        self,
        collection: str,
        job_id: str,
        kind: Literal["import", "rebuild"],
        fingerprint: str,
        documents: Sequence[KnowledgeDocument],
        *,
        principal_id: str,
    ) -> KnowledgeMaintenanceJob:
        prior = await self._jobs.get(job_id)
        if prior:
            if prior.data["fingerprint"] != fingerprint:
                raise OpsMeshError("CONFLICT", "Maintenance request key has different content")
            job = KnowledgeMaintenanceJob.model_validate(prior.data["job"])
            if job.status == "succeeded":
                return job.model_copy(update={"revision": prior.revision})
            row = prior
        else:
            job = KnowledgeMaintenanceJob(
                id=job_id,
                collection=collection,
                kind=kind,
                total=len(documents),
                document_ids=tuple(d.id for d in documents),
            )
            row = await self._jobs.create(
                job_id,
                {"collection": collection, "fingerprint": fingerprint, "job": job.model_dump()},
            )
        try:
            for index, document in enumerate(documents):
                await self._authorize(principal_id, f"knowledge:write:{collection}")
                await self._ensure_collection(collection)
                if kind == "import":
                    existing = await self._documents.get(document.id)
                    if existing is None:
                        await self.add(document, principal_id=principal_id)
                    elif existing.data != document.model_dump(mode="json"):
                        raise OpsMeshError("CONFLICT", "Imported document changed during retry")
                else:
                    vector = (await self._embed((document.title + " " + document.text,)))[0]
                    key = self._vector_key(document)
                    vector_row = await self._vectors.get(key)
                    data = {
                        "values": list(vector),
                        "provider": self.embeddings.id,
                        "document_id": document.id,
                    }
                    if vector_row:
                        await self._vectors.update(key, data, vector_row.revision)
                    else:
                        await self._store_vector(document, vector)
                job = job.model_copy(
                    update={"completed": index + 1, "status": "running", "error_code": None}
                )
                row = await self._jobs.update(
                    job_id, {**row.data, "job": job.model_dump()}, row.revision
                )
        except Exception as error:
            job = job.model_copy(
                update={
                    "status": "failed",
                    "error_code": error.code
                    if isinstance(error, OpsMeshError)
                    else "KNOWLEDGE_JOB_FAILED",
                }
            )
            await self._jobs.update(job_id, {**row.data, "job": job.model_dump()}, row.revision)
            raise
        job = job.model_copy(update={"status": "succeeded"})
        row = await self._jobs.update(job_id, {**row.data, "job": job.model_dump()}, row.revision)
        return job.model_copy(update={"revision": row.revision})
