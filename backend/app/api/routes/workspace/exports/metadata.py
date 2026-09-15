import json

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceExportRequest,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.domains.workspace.data_transfer.importers.metadata import (
    WorkspaceMetadataImportService,
)
from backend.app.domains.workspace.data_transfer.service import WorkspaceExportService
from backend.app.domains.workspace.storage.security import content_disposition_attachment

router = APIRouter()


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
    return WorkspaceMetadataImportService(session).import_metadata(
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
    return WorkspaceMetadataImportService(session).import_metadata(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=preview_request,
    )
