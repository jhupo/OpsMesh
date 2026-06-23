import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_archive_import import WorkspaceArchiveImportService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.files.storage import create_storage

router = APIRouter()


class ArchiveImportForm:
    def __init__(
        self,
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
    ) -> None:
        self.import_agents = import_agents
        self.import_teams = import_teams
        self.import_tasks = import_tasks
        self.import_file_bytes = import_file_bytes
        self.import_artifact_bytes = import_artifact_bytes
        self.name_prefix = name_prefix
        self.max_items_per_collection = max_items_per_collection
        self.max_bytes_per_object = max_bytes_per_object
        self.max_total_bytes = max_total_bytes
        self.resolutions = parse_archive_resolutions(resolutions)

    def request(self, *, dry_run: bool) -> WorkspaceArchiveImportRequest:
        return WorkspaceArchiveImportRequest(
            dry_run=dry_run,
            import_agents=self.import_agents,
            import_teams=self.import_teams,
            import_tasks=self.import_tasks,
            import_file_bytes=self.import_file_bytes,
            import_artifact_bytes=self.import_artifact_bytes,
            name_prefix=self.name_prefix,
            max_items_per_collection=self.max_items_per_collection,
            max_bytes_per_object=self.max_bytes_per_object,
            max_total_bytes=self.max_total_bytes,
            resolutions=self.resolutions,
        )


@router.post("/archive/import", response_model=WorkspaceImportResponse)
async def import_workspace_archive(
    file: UploadFile = File(...),
    dry_run: bool = Form(default=True),
    form: ArchiveImportForm = Depends(),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceImportResponse:
    return await run_archive_import(
        file=file,
        request=form.request(dry_run=dry_run),
        context=context,
        session=session,
        settings=settings,
    )


@router.post("/archive/import/preview", response_model=WorkspaceImportResponse)
async def preview_workspace_archive_import(
    file: UploadFile = File(...),
    form: ArchiveImportForm = Depends(),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceImportResponse:
    return await run_archive_import(
        file=file,
        request=form.request(dry_run=True),
        context=context,
        session=session,
        settings=settings,
    )


async def run_archive_import(
    *,
    file: UploadFile,
    request: WorkspaceArchiveImportRequest,
    context: WorkspaceContext,
    session: Session,
    settings: Settings,
) -> WorkspaceImportResponse:
    content = await file.read()
    try:
        return WorkspaceArchiveImportService(session).import_archive(
            workspace=context.workspace,
            user_id=context.user.user_id,
            archive_bytes=content,
            request=request,
            storage=create_storage(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def parse_archive_resolutions(value: str | None) -> dict[str, dict[str, object]]:
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
    return {str(key): value for key, value in parsed.items() if isinstance(value, dict)}
