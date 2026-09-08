from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.projects import (
    WorkspaceProjectCreateRequest,
    WorkspaceProjectDetailResponse,
    WorkspaceProjectFileCreateRequest,
    WorkspaceProjectFileResponse,
    WorkspaceProjectOutputCreateRequest,
    WorkspaceProjectOutputResponse,
    WorkspaceProjectResponse,
    WorkspaceProjectUpdateRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.projects.contracts import (
    ProjectCreateCommand,
    ProjectFileCommand,
    ProjectOutputCommand,
    ProjectUpdateCommand,
)
from backend.app.projects.service import WorkspaceProjectService

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["projects"])


@router.get("", response_model=PageResponse[WorkspaceProjectResponse])
async def list_projects(
    page: PageParams = Depends(pagination_params),
    include_archived: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceProjectResponse]:
    items, total = WorkspaceProjectService(session).list_projects(
        context.workspace.id,
        limit=page.limit,
        offset=page.offset,
        include_archived=include_archived,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WorkspaceProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: WorkspaceProjectCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectResponse:
    try:
        project = WorkspaceProjectService(session).create_project(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            command=ProjectCreateCommand(**request.model_dump()),
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WorkspaceProjectResponse.model_validate(project)


@router.get("/{project_id}", response_model=WorkspaceProjectDetailResponse)
async def get_project(
    project_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectDetailResponse:
    service = WorkspaceProjectService(session)
    project = service.get_project(context.workspace.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return WorkspaceProjectDetailResponse(
        project=WorkspaceProjectResponse.model_validate(project),
        input_files=[
            WorkspaceProjectFileResponse.model_validate(item)
            for item in service.list_input_files(context.workspace.id, project_id) or []
        ],
        outputs=[
            WorkspaceProjectOutputResponse.model_validate(item)
            for item in service.list_outputs(context.workspace.id, project_id) or []
        ],
    )


@router.patch("/{project_id}", response_model=WorkspaceProjectResponse)
async def update_project(
    project_id: UUID,
    request: WorkspaceProjectUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectResponse:
    try:
        project = WorkspaceProjectService(session).update_project(
            workspace_id=context.workspace.id,
            project_id=project_id,
            actor_user_id=context.user.user_id,
            command=ProjectUpdateCommand(**request.model_dump()),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return WorkspaceProjectResponse.model_validate(project)


@router.post("/{project_id}/archive", response_model=WorkspaceProjectResponse)
async def archive_project(
    project_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectResponse:
    project = WorkspaceProjectService(session).archive_project(
        workspace_id=context.workspace.id,
        project_id=project_id,
        actor_user_id=context.user.user_id,
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return WorkspaceProjectResponse.model_validate(project)


@router.post(
    "/{project_id}/input-files",
    response_model=WorkspaceProjectFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_input_file(
    project_id: UUID,
    request: WorkspaceProjectFileCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectFileResponse:
    try:
        binding = WorkspaceProjectService(session).add_input_file(
            workspace_id=context.workspace.id,
            project_id=project_id,
            actor_user_id=context.user.user_id,
            command=ProjectFileCommand(**request.model_dump()),
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(exc).lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return WorkspaceProjectFileResponse.model_validate(binding)


@router.delete(
    "/{project_id}/input-files/{project_file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_project_input_file(
    project_id: UUID,
    project_file_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> Response:
    removed = WorkspaceProjectService(session).remove_input_file(
        workspace_id=context.workspace.id,
        project_id=project_id,
        project_file_id=project_file_id,
        actor_user_id=context.user.user_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project input file not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/outputs",
    response_model=WorkspaceProjectOutputResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_output(
    project_id: UUID,
    request: WorkspaceProjectOutputCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceProjectOutputResponse:
    try:
        output = WorkspaceProjectService(session).add_output(
            workspace_id=context.workspace.id,
            project_id=project_id,
            actor_user_id=context.user.user_id,
            command=ProjectOutputCommand(**request.model_dump()),
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if output is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return WorkspaceProjectOutputResponse.model_validate(output)


@router.delete("/{project_id}/outputs/{project_output_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_output(
    project_id: UUID,
    project_output_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> Response:
    removed = WorkspaceProjectService(session).remove_output(
        workspace_id=context.workspace.id,
        project_id=project_id,
        project_output_id=project_output_id,
        actor_user_id=context.user.user_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project output not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
