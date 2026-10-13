from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.runtime.instances.schemas import (
    RuntimeCommandRequest,
    RuntimeCommandResponse,
    RuntimeEnvironmentCreateRequest,
    RuntimeEventResponse,
    RuntimeTemplateResponse,
    WorkspaceRuntimeResponse,
)
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runtimes"])


def _authorize_runtime(
    session: Session,
    context: WorkspaceContext,
    runtime_id: UUID,
    action: ResourceAction,
) -> None:
    ResourceAuthorizationService(session, context.user).require(
        context.workspace.id, ResourceKind.RUNTIME, runtime_id, action
    )


@router.get("/runtime-templates", response_model=list[RuntimeTemplateResponse])
def list_runtime_templates(
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[RuntimeTemplateResponse]:
    templates = RuntimeEnvironmentService(session, settings).list_templates()
    return [RuntimeTemplateResponse.model_validate(item) for item in templates]


@router.post(
    "/runtimes",
    response_model=WorkspaceRuntimeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_runtime(
    request: RuntimeEnvironmentCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRuntimeResponse:
    runtime = RuntimeEnvironmentService(session, settings).create_runtime(
        workspace_id=context.workspace.id,
        template_id=request.template_id,
        name=request.name,
        runtime_space_id=request.runtime_space_id,
    )
    return WorkspaceRuntimeResponse.model_validate(runtime)


@router.get("/runtimes", response_model=PageResponse[WorkspaceRuntimeResponse])
def list_runtimes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    runtime_status: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[WorkspaceRuntimeResponse]:
    items, total = RuntimeEnvironmentService(session, settings).list_runtimes(
        context.workspace.id, limit=limit, offset=offset, status=runtime_status
    )
    return PageResponse(
        items=[WorkspaceRuntimeResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/runtimes/{runtime_id}/enable", response_model=WorkspaceRuntimeResponse)
def enable_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRuntimeResponse:
    _authorize_runtime(session, context, runtime_id, ResourceAction.CONTROL)
    runtime = RuntimeEnvironmentService(session, settings).set_status(
        context.workspace.id, runtime_id, "active"
    )
    return WorkspaceRuntimeResponse.model_validate(runtime)


@router.post("/runtimes/{runtime_id}/disable", response_model=WorkspaceRuntimeResponse)
def disable_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRuntimeResponse:
    _authorize_runtime(session, context, runtime_id, ResourceAction.CONTROL)
    runtime = RuntimeEnvironmentService(session, settings).set_status(
        context.workspace.id, runtime_id, "disabled"
    )
    return WorkspaceRuntimeResponse.model_validate(runtime)


@router.delete("/runtimes/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> None:
    _authorize_runtime(session, context, runtime_id, ResourceAction.CONTROL)
    RuntimeEnvironmentService(session, settings).set_status(
        context.workspace.id, runtime_id, "deleted"
    )


@router.post(
    "/runtimes/{runtime_id}/commands",
    response_model=RuntimeCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_runtime_command(
    runtime_id: UUID,
    request: RuntimeCommandRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RuntimeCommandResponse:
    _authorize_runtime(session, context, runtime_id, ResourceAction.INVOKE)
    command = RuntimeEnvironmentService(session, settings).queue_command(
        workspace_id=context.workspace.id,
        runtime_id=runtime_id,
        command=request.command,
        requested_by_user_id=context.user.user_id,
    )
    if command is None:
        raise HTTPException(status_code=404, detail="Execution environment not found")
    return RuntimeCommandResponse.model_validate(command)


@router.get("/runtimes/{runtime_id}/commands", response_model=PageResponse[RuntimeCommandResponse])
def list_runtime_commands(
    runtime_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[RuntimeCommandResponse]:
    _authorize_runtime(session, context, runtime_id, ResourceAction.READ)
    items, total = RuntimeEnvironmentService(session, settings).list_commands(
        context.workspace.id, runtime_id, limit=limit, offset=offset
    )
    return PageResponse(
        items=[RuntimeCommandResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runtimes/{runtime_id}/events", response_model=PageResponse[RuntimeEventResponse])
def list_runtime_events(
    runtime_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[RuntimeEventResponse]:
    _authorize_runtime(session, context, runtime_id, ResourceAction.READ)
    items, total = RuntimeEnvironmentService(session, settings).list_events(
        context.workspace.id, runtime_id, limit=limit, offset=offset
    )
    return PageResponse(
        items=[RuntimeEventResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
