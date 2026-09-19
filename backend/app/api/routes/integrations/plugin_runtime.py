"""Remote plugin service transport; plugin credentials never authenticate as users."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from opsmesh_plugin_sdk.contracts import AcceptedEvent, EventState, IncomingMessage
from opsmesh_plugin_sdk.services import (
    PermissionQuery,
    PermissionResult,
    PluginLog,
    StoredValue,
    StoreWrite,
)
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.client_ip import security_request_context
from backend.app.api.dependencies.redis import get_redis_client
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.capabilities.plugins.services import PluginPrincipal, PluginServices
from backend.app.domains.integrations.automation_stream import AutomationStreamService
from backend.app.domains.integrations.automations import AutomationService
from backend.app.domains.orchestration.tasks.events import RedisTaskEventBus
from backend.app.observability.audit.security_events import SecurityAuditService

router = APIRouter(prefix="/plugin-runtime/{workspace_id}/{install_id}", tags=["plugin-runtime"])

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


async def plugin_principal(
    request: Request,
    workspace_id: UUID,
    install_id: UUID,
    authorization: str = Header(default=""),
    session: Session = Depends(get_db_session),
) -> AsyncIterator[PluginPrincipal]:
    scheme, _, token = authorization.partition(" ")
    try:
        if scheme.lower() != "bearer":
            raise ResourceAccessDenied()
        yield PluginServices(session).authenticate(workspace_id, install_id, token)
    except ResourceAccessDenied:
        session.rollback()
        SecurityAuditService(session).record_request_event(
            request_context=security_request_context(request),
            action="auth.plugin.rejected",
            outcome="denied",
            severity="warning",
            reason="Plugin service access denied",
            metadata={"install_id": str(install_id)},
        )
        session.commit()
        raise


@router.get("/configuration")
def configuration(
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    result = PluginServices(session).read(principal, "configuration")
    return result.value if result else {}


@router.post("/permissions", response_model=PermissionResult)
def permissions(
    request: PermissionQuery,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> PermissionResult:
    try:
        return PluginServices(session).permissions(principal, request)
    except ValueError as exc:
        raise HTTPException(400, "Invalid resource kind") from exc


@router.get("/storage", response_model=list[StoredValue])
def values(
    prefix: str = Query(default="", max_length=120),
    offset: int = Query(default=0, ge=0, le=512),
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> list[StoredValue]:
    return PluginServices(session).values(principal, prefix, offset)


@router.delete("/storage/{key}", status_code=204)
def delete_value(
    key: str,
    expected_revision: int = Query(ge=1),
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> None:
    try:
        PluginServices(session).delete(principal, key, expected_revision)
    except ValueError as exc:
        raise HTTPException(400, "Invalid storage key") from exc
    session.commit()


@router.get("/storage/{key}", response_model=StoredValue)
def read_value(
    key: str,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> StoredValue:
    try:
        result = PluginServices(session).read(principal, key)
    except ValueError as exc:
        raise HTTPException(400, "Invalid storage key") from exc
    if result is None:
        raise HTTPException(404, "Plugin value not found")
    return result


@router.put("/storage/{key}", response_model=StoredValue)
def write_value(
    key: str,
    request: StoreWrite,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> StoredValue:
    try:
        result = PluginServices(session).write(principal, key, request)
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin value") from exc


@router.post("/logs", status_code=204)
def log_event(
    request: PluginLog,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> None:
    try:
        PluginServices(session).log(principal, request)
        session.commit()
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin log") from exc


@router.post("/automations/{automation_id}/events", response_model=AcceptedEvent, status_code=202)
def receive(
    automation_id: UUID,
    request: IncomingMessage,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return AutomationService(session).receive(
            principal.workspace_id, automation_id, principal, request
        )
    except ValueError as exc:
        raise HTTPException(400, "Message admission rejected") from exc


@router.get("/automations/{automation_id}/events/{event_id}", response_model=EventState)
def state(
    automation_id: UUID,
    event_id: UUID,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> EventState:
    service = AutomationStreamService(session)
    try:
        event, _ = service.authorize(principal.workspace_id, automation_id, event_id, principal)
        return service.state(event)
    except ValueError as exc:
        raise HTTPException(403, "Message unavailable") from exc


@router.get("/automations/{automation_id}/events/{event_id}/stream")
async def stream(
    automation_id: UUID,
    event_id: UUID,
    cursor: str = Query(default="0-0", pattern=r"^\d{1,20}-\d{1,20}$"),
    once: bool = False,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    service = AutomationStreamService(session)
    try:
        service.authorize(principal.workspace_id, automation_id, event_id, principal)
    except ValueError as exc:
        raise HTTPException(403, "Message unavailable") from exc
    session.rollback()

    async def frames() -> AsyncIterator[str]:
        async for frame in service.frames(
            bus=RedisTaskEventBus(redis, settings.redis_key_prefix),
            workspace_id=principal.workspace_id,
            automation_id=automation_id,
            event_id=event_id,
            user=principal,
            cursor=cursor,
            once=once,
        ):
            yield frame.model_dump_json() + "\n"

    return StreamingResponse(
        frames(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
