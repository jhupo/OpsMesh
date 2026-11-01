"""Protected resource administration routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.capabilities.api import KnowledgeDocument, KnowledgeSearchHit
from opsmesh.modules.capabilities.knowledge import (
    KnowledgeCollection,
    KnowledgeDocumentRecord,
    KnowledgeImportRequest,
    KnowledgeMaintenanceJob,
)

from ..security import CurrentUser, get_application

router = APIRouter(tags=["capabilities"])


@router.post("/knowledge/documents", status_code=201)
async def add_document(body: KnowledgeDocument, request: Request, user: CurrentUser) -> Response:
    await get_application(request).services.knowledge_management.add(body, principal_id=user.id)
    return Response(status_code=201)


@router.get("/knowledge/{collection}/search")
async def search_knowledge(
    collection: str,
    request: Request,
    user: CurrentUser,
    query: Annotated[str, Query(min_length=1, max_length=10000)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    mode: Literal["lexical", "vector", "hybrid"] = "hybrid",
) -> tuple[KnowledgeSearchHit, ...]:
    return await get_application(request).services.knowledge_management.search(
        collection, query, principal_id=user.id, limit=limit, mode=mode
    )


class UpdateKnowledgeCollectionRequest(OpsMeshContract):
    collection: KnowledgeCollection
    expected_revision: int = Field(ge=1)


class UpdateKnowledgeDocumentRequest(OpsMeshContract):
    document: KnowledgeDocument
    expected_revision: int = Field(ge=1)


class RebuildKnowledgeRequest(OpsMeshContract):
    idempotency_key: str = Field(min_length=1, max_length=200)


@router.post("/knowledge/collections", status_code=201)
async def create_collection(
    body: KnowledgeCollection, request: Request, user: CurrentUser
) -> KnowledgeCollection:
    return await get_application(request).services.knowledge_management.create_collection(
        body, principal_id=user.id
    )


@router.get("/knowledge/collections")
async def collections(request: Request, user: CurrentUser) -> tuple[KnowledgeCollection, ...]:
    return await get_application(request).services.knowledge_management.list_collections(
        principal_id=user.id
    )


@router.get("/knowledge/collections/{collection_id}")
async def collection(
    collection_id: str, request: Request, user: CurrentUser
) -> KnowledgeCollection:
    return await get_application(request).services.knowledge_management.get_collection(
        collection_id, principal_id=user.id
    )


@router.put("/knowledge/collections/{collection_id}")
async def update_collection(
    collection_id: str, body: UpdateKnowledgeCollectionRequest, request: Request, user: CurrentUser
) -> KnowledgeCollection:
    if collection_id != body.collection.id:
        raise OpsMeshError("CONFIG_INVALID", "Collection identity cannot change")
    return await get_application(request).services.knowledge_management.update_collection(
        body.collection, principal_id=user.id, expected_revision=body.expected_revision
    )


@router.delete("/knowledge/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: str,
    request: Request,
    user: CurrentUser,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    await get_application(request).services.knowledge_management.delete_collection(
        collection_id, principal_id=user.id, expected_revision=expected_revision
    )
    return Response(status_code=204)


@router.get("/knowledge/collections/{collection_id}/documents")
async def documents(
    collection_id: str, request: Request, user: CurrentUser
) -> tuple[KnowledgeDocument, ...]:
    return await get_application(request).services.knowledge_management.list_documents(
        collection_id, principal_id=user.id
    )


@router.get("/knowledge/documents/{document_id}")
async def document(
    document_id: str, request: Request, user: CurrentUser
) -> KnowledgeDocumentRecord:
    service = get_application(request).services.knowledge_management
    return await service.get_document_record(document_id, principal_id=user.id)


@router.put("/knowledge/documents/{document_id}", status_code=204)
async def update_document(
    document_id: str, body: UpdateKnowledgeDocumentRequest, request: Request, user: CurrentUser
) -> Response:
    if document_id != body.document.id:
        raise OpsMeshError("CONFIG_INVALID", "Document identity cannot change")
    await get_application(request).services.knowledge_management.update_document(
        body.document, principal_id=user.id, expected_revision=body.expected_revision
    )
    return Response(status_code=204)


@router.delete("/knowledge/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    request: Request,
    user: CurrentUser,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    await get_application(request).services.knowledge_management.delete_document(
        document_id, principal_id=user.id, expected_revision=expected_revision
    )
    return Response(status_code=204)


@router.post("/knowledge/collections/{collection_id}/imports")
async def import_documents(
    collection_id: str, body: KnowledgeImportRequest, request: Request, user: CurrentUser
) -> KnowledgeMaintenanceJob:
    return await get_application(request).services.knowledge_management.import_texts(
        collection_id, body, principal_id=user.id
    )


@router.post("/knowledge/collections/{collection_id}/rebuild")
async def rebuild_embeddings(
    collection_id: str, body: RebuildKnowledgeRequest, request: Request, user: CurrentUser
) -> KnowledgeMaintenanceJob:
    return await get_application(request).services.knowledge_management.rebuild_embeddings(
        collection_id, principal_id=user.id, idempotency_key=body.idempotency_key
    )


@router.get("/knowledge/jobs/{job_id}")
async def maintenance_job(
    job_id: str, request: Request, user: CurrentUser
) -> KnowledgeMaintenanceJob:
    return await get_application(request).services.knowledge_management.get_maintenance_job(
        job_id, principal_id=user.id
    )
