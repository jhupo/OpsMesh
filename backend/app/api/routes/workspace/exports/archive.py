from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.core.common.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.workspace.projects.exports.contracts import WorkspaceArchiveExportRequest
from backend.app.domains.workspace.projects.exports.service import WorkspaceExportService
from backend.app.domains.workspace.storage.security import content_disposition_attachment
from backend.app.domains.workspace.storage.storage import create_storage

router = APIRouter()


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
        storage=create_storage(settings),
    )
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": content_disposition_attachment(result.filename)},
    )
