from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.resources.files.security import content_disposition_attachment
from opsmesh.resources.storage.storage import create_storage
from opsmesh.resources.transfers.contracts import WorkspaceArchiveExportRequest
from opsmesh.resources.transfers.service import WorkspaceExportService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session

router = APIRouter()


@router.post("/archive", status_code=status.HTTP_200_OK)
def export_workspace_archive(
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
