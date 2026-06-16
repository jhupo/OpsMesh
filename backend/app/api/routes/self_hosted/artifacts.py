from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.routes.self_hosted.dependencies import self_hosted_service
from backend.app.api.schemas.self_hosted import (
    ArtifactUploadRequest,
    ArtifactUploadResponse,
    LocalFileReferenceRequest,
    LocalFileReferenceResponse,
)
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.service import SelfHostedRuntimeService
from backend.app.self_hosted.types import AuthenticatedWorker

router = APIRouter()


@router.post(
    "/self-hosted/local-files",
    response_model=LocalFileReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_local_file_reference(
    request: LocalFileReferenceRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> LocalFileReferenceResponse:
    try:
        reference = service.create_local_file_reference(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return LocalFileReferenceResponse.model_validate(reference)


@router.post(
    "/self-hosted/artifact-uploads",
    response_model=ArtifactUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_artifact_upload(
    request: ArtifactUploadRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> ArtifactUploadResponse:
    try:
        upload = service.register_artifact_upload(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ArtifactUploadResponse.model_validate(upload)
