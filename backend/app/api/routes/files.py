from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.files import (
    ArtifactHistoryResponse,
    ArtifactResponse,
    FinalOutputArtifactHistoryResponse,
    WorkspaceFileResponse,
)
from backend.app.api.services.files import WorkspaceFileService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.files.security import content_disposition_attachment
from backend.app.files.storage import LocalStorage

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["files"])


def file_service(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceFileService:
    return WorkspaceFileService(
        session=session,
        storage=LocalStorage(settings.storage_root),
        max_upload_bytes=settings.max_upload_bytes,
    )


@router.get("/files", response_model=PageResponse[WorkspaceFileResponse])
async def list_files(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> PageResponse[WorkspaceFileResponse]:
    items, total = service.list_files(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/files", response_model=WorkspaceFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    service: WorkspaceFileService = Depends(file_service),
) -> WorkspaceFileResponse:
    content = await file.read()
    try:
        stored_file = service.upload_file(
            workspace_id=context.workspace.id,
            uploaded_by_user_id=context.user.user_id,
            filename=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    return WorkspaceFileResponse.model_validate(stored_file)


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> Response:
    try:
        file, content = service.read_file(context.workspace.id, file_id, context.user.user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=file.content_type,
        headers={"Content-Disposition": content_disposition_attachment(file.filename)},
    )


@router.get("/artifacts", response_model=PageResponse[ArtifactResponse])
async def list_artifacts(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> PageResponse[ArtifactResponse]:
    items, total = service.list_artifacts(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/artifacts/history", response_model=ArtifactHistoryResponse)
async def get_artifact_history(
    task_id: UUID = Query(),
    work_package_id: str = Query(min_length=1, max_length=120),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> ArtifactHistoryResponse:
    items = service.list_artifact_history(
        workspace_id=context.workspace.id,
        task_id=task_id,
        work_package_id=work_package_id,
    )
    latest = items[0] if items else None
    return ArtifactHistoryResponse(
        task_id=task_id,
        work_package_id=work_package_id,
        items=[ArtifactResponse.model_validate(item) for item in items],
        total=len(items),
        latest_artifact_id=latest.id if latest is not None else None,
        latest_version=latest.version if latest is not None else None,
    )


@router.get(
    "/artifacts/final-output/history",
    response_model=FinalOutputArtifactHistoryResponse,
)
async def get_final_output_artifact_history(
    task_id: UUID = Query(),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> FinalOutputArtifactHistoryResponse:
    task, work_package_ids, items = service.list_final_output_artifact_history(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    latest = items[0] if items else None
    return FinalOutputArtifactHistoryResponse(
        task_id=task_id,
        final_output=task.final_output if task is not None else None,
        final_work_package_ids=work_package_ids,
        items=[ArtifactResponse.model_validate(item) for item in items],
        total=len(items),
        latest_artifact_id=latest.id if latest is not None else None,
        latest_version=latest.version if latest is not None else None,
    )


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    service: WorkspaceFileService = Depends(file_service),
) -> Response:
    try:
        artifact, content = service.read_artifact(
            context.workspace.id,
            artifact_id,
            context.user.user_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=artifact.content_type,
        headers={"Content-Disposition": content_disposition_attachment(artifact.filename)},
    )
