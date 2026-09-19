from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from opsmesh_plugin_sdk.cards import CardTemplate
from opsmesh_plugin_sdk.contracts import (
    AcceptedEvent,
    AutomationReply,
    AutomationStreamEvent,
    EventState,
    IncomingMessage,
    PluginManifest,
)
from opsmesh_plugin_sdk.distribution import PluginCatalog, SignedPluginRelease
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.api.dependencies.redis import get_redis_client
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.errors import AuthorizationError
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.integrations.automation_contracts import (
    AutomationConfiguration,
    AutomationResponse,
    AutomationUpdate,
    ExternalIdentityRequest,
    ExternalIdentityResponse,
)
from backend.app.domains.integrations.automation_stream import AutomationStreamService
from backend.app.domains.integrations.automations import AutomationService
from backend.app.domains.integrations.identities import ExternalIdentityService
from backend.app.domains.orchestration.tasks.events import RedisTaskEventBus

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

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
        "stream_event": AutomationStreamEvent.model_json_schema(),
        "plugin_manifest": PluginManifest.model_json_schema(),
        "plugin_installation_supported": True,
        "plugin_distribution_supported": True,
        "plugin_catalog": PluginCatalog.model_json_schema(),
        "plugin_release": SignedPluginRelease.model_json_schema(),
        "plugin_execution_modes": ["remote"],
        "plugin_service_credentials_supported": True,
        "card_template": CardTemplate.model_json_schema(),
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


@router.put("/{automation_id}/identities/{sender_id}", response_model=ExternalIdentityResponse)
def set_external_identity(
    automation_id: UUID,
    sender_id: str,
    request: ExternalIdentityRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ExternalIdentityResponse:
    if not sender_id or len(sender_id) > 160:
        raise HTTPException(422, "Sender ID must contain between 1 and 160 characters")
    binding = ExternalIdentityService(session).set_binding(
        workspace_id=context.workspace.id,
        automation_id=automation_id,
        sender_id=sender_id,
        user_id=request.user_id,
        active=request.active,
        actor=context.user,
    )
    session.commit()
    return ExternalIdentityResponse.model_validate(binding)


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
            user_id=context.user.user_id,
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
        service = AutomationStreamService(session)
        event, _ = service.authorize(context.workspace.id, automation_id, event_id, context.user)
        return service.state(event)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{automation_id}/events/{event_id}/stream")
async def stream_event(
    automation_id: UUID,
    event_id: UUID,
    cursor: str = Query(default="0-0", pattern=r"^\d{1,20}-\d{1,20}$"),
    once: bool = False,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    service = AutomationStreamService(session)
    try:
        service.authorize(context.workspace.id, automation_id, event_id, context.user)
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(403, "Message stream unavailable") from exc
    workspace_id, user = context.workspace.id, context.user
    session.rollback()

    async def frames() -> AsyncIterator[str]:
        async for frame in service.frames(
            bus=RedisTaskEventBus(redis, settings.redis_key_prefix),
            workspace_id=workspace_id,
            automation_id=automation_id,
            event_id=event_id,
            user=user,
            cursor=cursor,
            once=once,
        ):
            yield frame.model_dump_json() + "\n"

    return StreamingResponse(
        frames(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
