from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from opsmesh_plugin_sdk.contracts import (
    AcceptedEvent,
    AutomationReply,
    EventState,
    IncomingMessage,
    PluginManifest,
)
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.integrations.automation_contracts import (
    AutomationConfiguration,
    AutomationResponse,
    AutomationUpdate,
)
from backend.app.domains.integrations.automations import AutomationService

router = APIRouter(prefix="/workspaces/{workspace_id}/automations", tags=["automations"])


@router.get("/configuration-contracts")
def configuration_contracts(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> dict[str, object]:
    return {
        "automation": AutomationConfiguration.model_json_schema(),
        "message": IncomingMessage.model_json_schema(),
        "reply": AutomationReply.model_json_schema(),
        "event_state": EventState.model_json_schema(),
        "plugin_manifest": PluginManifest.model_json_schema(),
        "plugin_installation_supported": True,
        "plugin_execution_modes": ["remote"],
    }


@router.get("", response_model=list[AutomationResponse])
def list_automations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    return AutomationService(session).list_automations(
        context.workspace.id,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=AutomationResponse, status_code=201)
def create_automation(
    request: AutomationConfiguration,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return AutomationService(session).create(
            context.workspace.id, context.user.user_id, request
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{automation_id}", response_model=AutomationResponse)
def update_automation(
    automation_id: UUID,
    request: AutomationUpdate,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return AutomationService(session).update(
            context.workspace.id,
            automation_id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{automation_id}/events", response_model=AcceptedEvent, status_code=202)
def receive_message(
    automation_id: UUID,
    request: IncomingMessage,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return AutomationService(session).receive(
            context.workspace.id,
            automation_id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{automation_id}/events", response_model=list[AcceptedEvent])
def list_events(
    automation_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return AutomationService(session).events(
            context.workspace.id,
            automation_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{automation_id}/events/{event_id}", response_model=EventState)
def event_state(
    automation_id: UUID,
    event_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> EventState:
    try:
        return AutomationService(session).event_state(context.workspace.id, automation_id, event_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
