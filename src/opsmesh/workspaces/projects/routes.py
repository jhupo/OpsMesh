"""Project lifecycle APIs: create, query, modify, archive and delete."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces.projects import service
from opsmesh.workspaces.projects.models import Project
from opsmesh.workspaces.projects.schemas import (
    ProjectCreateRequest,
    ProjectPage,
    ProjectUpdateRequest,
    ProjectView,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
ProjectIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _view(project: Project) -> ProjectView:
    return ProjectView(
        project_id=project.id,
        workspace_id=project.workspace_id,
        name=project.name,
        description=project.description,
        status=project.status,
        created_by_user_id=project.created_by_user_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post(
    "",
    response_model=ProjectView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.projects.create",
)
def create_project(
    payload: ProjectCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> ProjectView:
    """Create one project; the creator becomes its administrator."""
    with session_transaction(session):
        project = service.create_project(
            session,
            actor=user,
            workspace_id=workspace_id,
            name=payload.name,
            description=payload.description,
        )
    return _view(project)


@router.get("", response_model=ProjectPage, operation_id="workspaces.projects.list")
def list_projects(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ProjectPage:
    """List the projects of one workspace the current user may see."""
    projects, total = service.list_projects(
        session, actor=user, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return ProjectPage(projects=[_view(item) for item in projects], total=total)


@router.get("/{project_id}", response_model=ProjectView, operation_id="workspaces.projects.read")
def read_project(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
) -> ProjectView:
    return _view(
        service.get_project(session, actor=user, workspace_id=workspace_id, project_id=project_id)
    )


@router.patch(
    "/{project_id}", response_model=ProjectView, operation_id="workspaces.projects.update"
)
def update_project(
    payload: ProjectUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
) -> ProjectView:
    """Change the project metadata; an archived project refuses writes."""
    with session_transaction(session):
        project = service.update_project(
            session,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            name=payload.name,
            description=payload.description,
        )
    return _view(project)


@router.post(
    "/{project_id}/archive", response_model=ProjectView, operation_id="workspaces.projects.archive"
)
def archive_project(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
) -> ProjectView:
    """Archive one project; reads stay open, writes are refused."""
    with session_transaction(session):
        project = service.archive_project(
            session, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    return _view(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspaces.projects.delete",
)
def delete_project(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
) -> Response:
    """Delete one project; its history stays and every access is refused."""
    with session_transaction(session):
        service.delete_project(
            session, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
