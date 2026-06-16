from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveIntegrityResponse,
    WorkspaceArchiveRestoreDrillRequest,
    WorkspaceExportJobResponse,
    WorkspaceRestoreDrillResponse,
)
from backend.app.api.services.exports import WorkspaceExportService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.files.security import content_disposition_attachment
from backend.app.files.storage import create_storage
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

router = APIRouter()


@router.post(
    "/archive/jobs",
    response_model=WorkspaceExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_workspace_archive_export_job(
    request: WorkspaceArchiveExportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> WorkspaceExportJobResponse:
    export_job = WorkspaceExportService(session).create_archive_export_job(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=request,
        queue=queue,
    )
    return WorkspaceExportJobResponse.model_validate(export_job)


@router.get("/archive/jobs/{job_id}", response_model=WorkspaceExportJobResponse)
async def get_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceExportJobResponse:
    export_job = WorkspaceExportService(session).get_export_job(
        workspace_id=context.workspace.id,
        job_id=job_id,
    )
    if export_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    return WorkspaceExportJobResponse.model_validate(export_job)


@router.get("/archive/jobs/{job_id}/download")
async def download_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        export_job, content = WorkspaceExportService(session).read_export_job_content(
            workspace_id=context.workspace.id,
            job_id=job_id,
            storage=create_storage(settings),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=export_job.content_type or "application/zip",
        headers={
            "Content-Disposition": content_disposition_attachment(
                export_job.filename or "workspace-archive.zip"
            )
        },
    )


@router.post(
    "/archive/jobs/{job_id}/verify",
    response_model=WorkspaceArchiveIntegrityResponse,
)
async def verify_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceArchiveIntegrityResponse:
    try:
        result = WorkspaceExportService(session).verify_archive_export_job(
            workspace_id=context.workspace.id,
            job_id=job_id,
            user_id=context.user.user_id,
            storage=create_storage(settings),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WorkspaceArchiveIntegrityResponse.model_validate(result)


@router.post(
    "/archive/jobs/{job_id}/restore-drill",
    response_model=WorkspaceRestoreDrillResponse,
)
async def run_workspace_archive_restore_drill(
    job_id: UUID,
    request: WorkspaceArchiveRestoreDrillRequest = Body(
        default_factory=WorkspaceArchiveRestoreDrillRequest
    ),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRestoreDrillResponse:
    try:
        result = WorkspaceExportService(session).run_archive_restore_drill(
            workspace=context.workspace,
            user_id=context.user.user_id,
            job_id=job_id,
            request=request,
            storage=create_storage(settings),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WorkspaceRestoreDrillResponse.model_validate(result)
