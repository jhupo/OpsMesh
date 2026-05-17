import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveImportRequest,
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

router = APIRouter(prefix="/workspaces/{workspace_id}/exports", tags=["exports"])


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
