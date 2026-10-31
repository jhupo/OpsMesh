"""Memory namespaces and generation are distinct from sessions and checkpoints."""

from fastapi import APIRouter, Request, Response
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryJob,
    MemoryNamespace,
    MemoryNamespaceSettings,
    MemoryPolicy,
    MemoryRetentionResult,
    MemorySource,
)

from ..security import CurrentUser, authorize, get_application

router = APIRouter(prefix="/memory", tags=["memory"])


class CreateNamespaceRequest(OpsMeshContract):
    id: str = Field(min_length=1)
    readers: tuple[str, ...] = ()
    contributors: tuple[str, ...] = ()
    policy: MemoryPolicy = Field(default_factory=MemoryPolicy)


class GenerationRequest(OpsMeshContract):
    idempotency_key: str = Field(min_length=1, max_length=200)


class UpdateMemoryRequest(GenerationRequest):
    candidate: MemoryCandidate
    expected_revision: int = Field(ge=1)


@router.post("/namespaces", status_code=201)
async def create_namespace(
    body: CreateNamespaceRequest, request: Request, user: CurrentUser
) -> MemoryNamespace:
    await authorize(request, user, "memory:write", body.id, owner_id=user.id)
    namespace = MemoryNamespace(owner=user.id, **body.model_dump())
    return await get_application(request).services.memory_management.create_namespace(
        namespace, actor_id=user.id
    )


class ConfigureNamespaceRequest(OpsMeshContract):
    settings: MemoryNamespaceSettings
    expected_revision: int = Field(ge=1)


@router.get("/namespaces")
async def namespaces(request: Request, user: CurrentUser) -> tuple[MemoryNamespace, ...]:
    return await get_application(request).services.memory_management.list_namespaces(
        actor_id=user.id
    )


@router.put("/namespaces/{namespace_id}/settings")
async def configure_namespace(
    namespace_id: str, body: ConfigureNamespaceRequest, request: Request, user: CurrentUser
) -> MemoryNamespace:
    return await get_application(request).services.memory_management.configure_namespace(
        namespace_id,
        body.settings,
        actor_id=user.id,
        expected_revision=body.expected_revision,
    )


@router.get("/namespaces/{namespace_id}")
async def namespace(namespace_id: str, request: Request, user: CurrentUser) -> MemoryNamespace:
    return await get_application(request).services.memory_management.get_namespace(
        namespace_id, principal_id=user.id
    )


@router.get("/namespaces/{namespace_id}/content")
async def read(namespace_id: str, request: Request, user: CurrentUser) -> MemoryCandidate:
    return await get_application(request).services.memory_management.read(
        namespace_id, principal_id=user.id
    )


@router.put("/namespaces/{namespace_id}/content")
async def update(
    namespace_id: str, body: UpdateMemoryRequest, request: Request, user: CurrentUser
) -> MemoryNamespace:
    return await get_application(request).services.memory_management.update(
        namespace_id,
        body.candidate,
        principal_id=user.id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
    )


@router.post("/namespaces/{namespace_id}/prune")
async def prune(namespace_id: str, request: Request, user: CurrentUser) -> MemoryRetentionResult:
    return await get_application(request).services.memory_management.prune(
        namespace_id, principal_id=user.id
    )


@router.post("/namespaces/{namespace_id}/sources", status_code=201)
async def source(
    namespace_id: str, body: MemorySource, request: Request, user: CurrentUser
) -> MemorySource:
    if namespace_id != body.namespace_id:
        raise OpsMeshError("INVALID_REQUEST", "Source namespace differs from request path")
    app = get_application(request)
    run = await app.services.runtime.get_run(body.run_id)
    await authorize(request, user, "run:read", run.id, owner_id=run.principal_id)
    if run.session_id != body.conversation_id:
        raise OpsMeshError("INVALID_REQUEST", "Memory source run and conversation do not match")
    return await app.services.memory_management.append_source(body, principal_id=user.id)


@router.post("/namespaces/{namespace_id}/generate", status_code=202)
async def generate(
    namespace_id: str, body: GenerationRequest, request: Request, user: CurrentUser
) -> MemoryJob:
    return await get_application(request).services.memory_management.request_generation(
        principal_id=user.id, namespace_id=namespace_id, idempotency_key=body.idempotency_key
    )


@router.get("/namespaces/{namespace_id}/jobs")
async def jobs(namespace_id: str, request: Request, user: CurrentUser) -> tuple[MemoryJob, ...]:
    return await get_application(request).services.memory_management.list_jobs(
        namespace_id, principal_id=user.id
    )


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request, user: CurrentUser) -> MemoryJob:
    return await get_application(request).services.memory_management.get_job(
        job_id, principal_id=user.id
    )


@router.delete("/namespaces/{namespace_id}", status_code=204)
async def forget(namespace_id: str, request: Request, user: CurrentUser) -> Response:
    await get_application(request).services.memory_management.forget(
        namespace_id, principal_id=user.id
    )
    return Response(status_code=204)
