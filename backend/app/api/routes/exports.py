import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveImportRequest,
    WorkspaceDataLifecycleResponse,
    WorkspaceExportJobResponse,
    WorkspaceExportRequest,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.exports import WorkspaceExportService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.files.security import content_disposition_attachment
from backend.app.files.storage import LocalStorage
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService

router = APIRouter(prefix="/workspaces/{workspace_id}/exports", tags=["exports"])


@router.get("/lifecycle-diagnostics", response_model=WorkspaceDataLifecycleResponse)
async def get_workspace_data_lifecycle_diagnostics(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceDataLifecycleResponse:
    diagnostics = WorkspaceDataLifecycleService(session).get_diagnostics(
        workspace_id=context.workspace.id
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceDataLifecycleResponse.model_validate(diagnostics)


@router.post("/metadata", status_code=status.HTTP_200_OK)
async def export_workspace_metadata(
    request: WorkspaceExportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> Response:
    export = WorkspaceExportService(session).build_export(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=request,
    )
    filename = f"{context.workspace.slug}-workspace-export.json"
    return Response(
        content=json.dumps(export.model_dump(mode="json"), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": content_disposition_attachment(filename)},
    )


@router.post("/metadata/import", response_model=WorkspaceImportResponse)
async def import_workspace_metadata(
    request: WorkspaceImportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceImportResponse:
    return WorkspaceExportService(session).import_metadata(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=request,
    )


@router.post("/metadata/import/preview", response_model=WorkspaceImportResponse)
async def preview_workspace_metadata_import(
    request: WorkspaceImportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceImportResponse:
    preview_request = request.model_copy(update={"dry_run": True, "preview_token": None})
    return WorkspaceExportService(session).import_metadata(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=preview_request,
    )


@router.post("/archive", status_code=status.HTTP_200_OK)
async def export_workspace_archive(
    request: WorkspaceArchiveExportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    result = WorkspaceExportService(session).build_archive_export(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=request,
        storage=LocalStorage(settings.storage_root),
    )
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": content_disposition_attachment(result.filename)},
    )


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
            storage=LocalStorage(settings.storage_root),
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


@router.post("/archive/import", response_model=WorkspaceImportResponse)
async def import_workspace_archive(
    file: UploadFile = File(...),
    dry_run: bool = Form(default=True),
    import_agents: bool = Form(default=True),
    import_teams: bool = Form(default=True),
    import_tasks: bool = Form(default=True),
    import_file_bytes: bool = Form(default=True),
    import_artifact_bytes: bool = Form(default=True),
    name_prefix: str = Form(default="Imported "),
    max_items_per_collection: int = Form(default=500),
    max_bytes_per_object: int = Form(default=25 * 1024 * 1024),
    max_total_bytes: int = Form(default=100 * 1024 * 1024),
    resolutions: str | None = Form(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceImportResponse:
    content = await file.read()
    request = WorkspaceArchiveImportRequest(
        dry_run=dry_run,
        import_agents=import_agents,
        import_teams=import_teams,
        import_tasks=import_tasks,
        import_file_bytes=import_file_bytes,
        import_artifact_bytes=import_artifact_bytes,
        name_prefix=name_prefix,
        max_items_per_collection=max_items_per_collection,
        max_bytes_per_object=max_bytes_per_object,
        max_total_bytes=max_total_bytes,
        resolutions=_parse_archive_resolutions(resolutions),
    )
    try:
        return WorkspaceExportService(session).import_archive(
            workspace=context.workspace,
            user_id=context.user.user_id,
            archive_bytes=content,
            request=request,
            storage=LocalStorage(settings.storage_root),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/archive/import/preview", response_model=WorkspaceImportResponse)
async def preview_workspace_archive_import(
    file: UploadFile = File(...),
    import_agents: bool = Form(default=True),
    import_teams: bool = Form(default=True),
    import_tasks: bool = Form(default=True),
    import_file_bytes: bool = Form(default=True),
    import_artifact_bytes: bool = Form(default=True),
    name_prefix: str = Form(default="Imported "),
    max_items_per_collection: int = Form(default=500),
    max_bytes_per_object: int = Form(default=25 * 1024 * 1024),
    max_total_bytes: int = Form(default=100 * 1024 * 1024),
    resolutions: str | None = Form(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceImportResponse:
    content = await file.read()
    request = WorkspaceArchiveImportRequest(
        dry_run=True,
        import_agents=import_agents,
        import_teams=import_teams,
        import_tasks=import_tasks,
        import_file_bytes=import_file_bytes,
        import_artifact_bytes=import_artifact_bytes,
        name_prefix=name_prefix,
        max_items_per_collection=max_items_per_collection,
        max_bytes_per_object=max_bytes_per_object,
        max_total_bytes=max_total_bytes,
        resolutions=_parse_archive_resolutions(resolutions),
    )
    try:
        return WorkspaceExportService(session).import_archive(
            workspace=context.workspace,
            user_id=context.user.user_id,
            archive_bytes=content,
            request=request,
            storage=LocalStorage(settings.storage_root),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _parse_archive_resolutions(value: str | None) -> dict[str, dict[str, object]]:
    if value is None or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archive import resolutions must be valid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archive import resolutions must be a JSON object",
        )
    return {
        str(key): value
        for key, value in parsed.items()
        if isinstance(value, dict)
    }
