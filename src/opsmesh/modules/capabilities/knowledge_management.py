"""Authorized and audited knowledge operations for HTTP and plugin dependencies."""

from typing import Literal

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import KnowledgeDocument, KnowledgeSearchHit
from .internal.knowledge import LocalKnowledgeIndex
from .knowledge import (
    KnowledgeCollection,
    KnowledgeDocumentRecord,
    KnowledgeImportRequest,
    KnowledgeMaintenanceJob,
)


class KnowledgeManagement:
    def __init__(self, index: LocalKnowledgeIndex, audit: AuditJournal) -> None:
        self._index, self._audit = index, audit

    async def add(self, document: KnowledgeDocument, *, principal_id: str) -> None:
        async with audited_operation(
            self._audit, "knowledge.add", document.collection, principal_id
        ):
            await self._index.add(document, principal_id=principal_id)

    async def search(
        self,
        collection: str,
        query: str,
        *,
        principal_id: str,
        limit: int = 10,
        mode: Literal["lexical", "vector", "hybrid"] = "hybrid",
    ) -> tuple[KnowledgeSearchHit, ...]:
        return await self._index.search(
            collection, query, principal_id=principal_id, limit=limit, mode=mode
        )

    async def create_collection(
        self,
        collection: KnowledgeCollection,
        *,
        principal_id: str,
    ) -> KnowledgeCollection:
        async with audited_operation(
            self._audit, "knowledge.create_collection", collection.id, principal_id
        ):
            return await self._index.create_collection(collection, principal_id=principal_id)

    async def list_collections(self, *, principal_id: str) -> tuple[KnowledgeCollection, ...]:
        return await self._index.list_collections(principal_id=principal_id)

    async def get_collection(self, collection: str, *, principal_id: str) -> KnowledgeCollection:
        return await self._index.get_collection(collection, principal_id=principal_id)

    async def update_collection(
        self,
        collection: KnowledgeCollection,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> KnowledgeCollection:
        async with audited_operation(
            self._audit, "knowledge.update_collection", collection.id, principal_id
        ):
            return await self._index.update_collection(
                collection, principal_id=principal_id, expected_revision=expected_revision
            )

    async def delete_collection(
        self, collection: str, *, principal_id: str, expected_revision: int
    ) -> None:
        async with audited_operation(
            self._audit, "knowledge.delete_collection", collection, principal_id
        ):
            await self._index.delete_collection(
                collection, principal_id=principal_id, expected_revision=expected_revision
            )

    async def list_documents(
        self, collection: str, *, principal_id: str
    ) -> tuple[KnowledgeDocument, ...]:
        return await self._index.list_documents(collection, principal_id=principal_id)

    async def get_document(self, document_id: str, *, principal_id: str) -> KnowledgeDocument:
        return await self._index.get_document(document_id, principal_id=principal_id)

    async def get_document_record(
        self,
        document_id: str,
        *,
        principal_id: str,
    ) -> KnowledgeDocumentRecord:
        return await self._index.get_document_record(document_id, principal_id=principal_id)

    async def update_document(
        self,
        document: KnowledgeDocument,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> None:
        async with audited_operation(
            self._audit, "knowledge.update_document", document.id, principal_id
        ):
            await self._index.update_document(
                document, principal_id=principal_id, expected_revision=expected_revision
            )

    async def delete_document(
        self, document_id: str, *, principal_id: str, expected_revision: int
    ) -> None:
        async with audited_operation(
            self._audit, "knowledge.delete_document", document_id, principal_id
        ):
            await self._index.delete_document(
                document_id, principal_id=principal_id, expected_revision=expected_revision
            )

    async def import_texts(
        self,
        collection: str,
        request: KnowledgeImportRequest,
        *,
        principal_id: str,
    ) -> KnowledgeMaintenanceJob:
        async with audited_operation(self._audit, "knowledge.import", collection, principal_id):
            return await self._index.import_texts(collection, request, principal_id=principal_id)

    async def rebuild_embeddings(
        self,
        collection: str,
        *,
        principal_id: str,
        idempotency_key: str,
    ) -> KnowledgeMaintenanceJob:
        async with audited_operation(self._audit, "knowledge.rebuild", collection, principal_id):
            return await self._index.rebuild_embeddings(
                collection, principal_id=principal_id, idempotency_key=idempotency_key
            )

    async def get_maintenance_job(
        self, job_id: str, *, principal_id: str
    ) -> KnowledgeMaintenanceJob:
        return await self._index.get_maintenance_job(job_id, principal_id=principal_id)
