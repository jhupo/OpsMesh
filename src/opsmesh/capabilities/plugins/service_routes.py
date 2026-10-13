"""Remote plugin service transport; plugin credentials never authenticate as users."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import StreamingResponse
from opsmesh_plugin_sdk.context import PluginContext, UserContext, UserIdentity
from opsmesh_plugin_sdk.messaging.contracts import (
    ApprovalDecision,
    ApprovalReceipt,
    EventState,
)
from opsmesh_plugin_sdk.services.identity import PermissionQuery, PermissionResult
from opsmesh_plugin_sdk.services.knowledge import (
    KnowledgeHit,
    KnowledgeQuery,
    MemoryReceipt,
    MemoryWrite,
)
from opsmesh_plugin_sdk.services.observability import PluginLog
from opsmesh_plugin_sdk.services.resources import ResourcePage, ResourceQuery
from opsmesh_plugin_sdk.services.storage import StoredValue, StoreWrite
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.capabilities.plugins.configuration import PluginConfigurationService
from opsmesh.capabilities.plugins.logging import record_plugin_log
from opsmesh.capabilities.plugins.user_identity import PluginUserIdentityService
from opsmesh.capabilities.plugins.user_resources import PluginUserResourceService
from opsmesh.governance.security_events.service import SecurityEventRecorder
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.automations.plugin_messages import PluginApprovalDecisionService
from opsmesh.orchestration.automations.stream import AutomationStreamService
from opsmesh.orchestration.tasks.events import PostgresTaskEventReader
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.client_ip import security_request_context

router = APIRouter(prefix="/plugin-services/{workspace_id}/{install_id}", tags=["plugin-services"])


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
            raise ResourceAccessDeniedError()
        yield PluginAuthenticationService(session).authenticate(workspace_id, install_id, token)
    except ResourceAccessDeniedError:
        session.rollback()
        SecurityEventRecorder(session).record_request_event(
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
    result = PluginConfigurationService(session).read(principal, "configuration")
    return result.value if result else {}


@router.get("/context", response_model=PluginContext)
def installation_context(
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> PluginContext:
    return PluginAuthenticationService(session).context(principal)


@router.post("/identity", response_model=UserIdentity)
def user_identity(
    request: UserContext,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> UserIdentity:
    return PluginUserIdentityService(session).identity(principal, request)


@router.post("/resources/query", response_model=ResourcePage)
def query_resources(
    request: ResourceQuery,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> ResourcePage:
    return PluginUserResourceService(session).resources(principal, request)


@router.post("/knowledge/search", response_model=list[KnowledgeHit])
def search_knowledge(
    request: KnowledgeQuery,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> list[KnowledgeHit]:
    result = PluginUserResourceService(session).search(principal, request)
    session.commit()
    return result


@router.post("/memory", response_model=MemoryReceipt)
def remember(
    request: MemoryWrite,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> MemoryReceipt:
    try:
        result = PluginUserResourceService(session).remember(principal, request)
    except ValueError as exc:
        raise HTTPException(400, "Invalid memory request") from exc
    session.commit()
    return result


@router.post("/permissions", response_model=PermissionResult)
def permissions(
    request: PermissionQuery,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> PermissionResult:
    try:
        return PluginUserIdentityService(session).permissions(principal, request)
    except ValueError as exc:
        raise HTTPException(400, "Invalid resource kind") from exc


@router.get("/storage", response_model=list[StoredValue])
def values(
    prefix: str = Query(default="", max_length=120),
    offset: int = Query(default=0, ge=0, le=512),
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> list[StoredValue]:
    return PluginConfigurationService(session).values(principal, prefix, offset)


@router.delete("/storage/{key}", status_code=204)
def delete_value(
    key: str,
    expected_revision: int = Query(ge=1),
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> None:
    try:
        PluginConfigurationService(session).delete(principal, key, expected_revision)
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
        result = PluginConfigurationService(session).read(principal, key)
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
        result = PluginConfigurationService(session).write(principal, key, request)
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
        record_plugin_log(session, principal, request)
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin log") from exc


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


@router.get(
    "/automations/{automation_id}/events/{event_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}},
)
async def stream(
    automation_id: UUID,
    event_id: UUID,
    cursor: str = Query(default="0", min_length=1, max_length=36),
    once: bool = False,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    service = AutomationStreamService(session)
    try:
        service.authorize(principal.workspace_id, automation_id, event_id, principal)
    except ValueError as exc:
        raise HTTPException(403, "Message unavailable") from exc
    if cursor != "0":
        try:
            UUID(cursor)
        except ValueError as exc:
            raise HTTPException(422, "Invalid event cursor") from exc
    session.rollback()

    async def frames() -> AsyncIterator[str]:
        async for frame in service.frames(
            bus=PostgresTaskEventReader(session),
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


@router.post(
    "/automations/{automation_id}/events/{event_id}/approvals/{approval_id}/decision",
    response_model=ApprovalReceipt,
)
def decide_approval(
    automation_id: UUID,
    event_id: UUID,
    approval_id: UUID,
    request: ApprovalDecision,
    principal: PluginPrincipal = Depends(plugin_principal),
    session: Session = Depends(get_db_session),
) -> ApprovalReceipt:
    try:
        return PluginApprovalDecisionService(session).decide_approval(
            principal, automation_id, event_id, approval_id, request
        )
    except ValueError as exc:
        raise HTTPException(403, "Message unavailable") from exc
