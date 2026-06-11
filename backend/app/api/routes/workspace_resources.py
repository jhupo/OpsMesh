import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.agents.model_provider_summary import agent_profile_response
from backend.app.agents.service import AgentManagementService
from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.agents import (
    AgentProfileCloneRequest,
    AgentProfileCreateRequest,
    AgentProfileResponse,
    AgentProfileRollbackRequest,
    AgentProfileUpdateRequest,
    AgentProfileVersionResponse,
    AgentSessionClearResponse,
    AgentSessionCompactionResponse,
    AgentSessionCompactRequest,
    AgentSessionDetailResponse,
    AgentSessionSummaryResponse,
)
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.api.schemas.tasks import (
    TaskCorrectionDiagnosticsResponse,
    TaskCorrectionRequest,
    TaskCorrectionResponse,
    TaskCreateRequest,
    TaskExecutionDiagnosticsResponse,
    TaskHandoffQueueResponse,
    TaskLiveStatusResponse,
    TaskManagerDiagnosticsResponse,
    TaskManagerQueueResponse,
    TaskMessageResponse,
    TaskObservationResponse,
    TaskOperatorActionRequest,
    TaskOperatorActionResponse,
    TaskPlanDiagnosticsResponse,
    TaskPlanningAttemptResponse,
    TaskPlanRegenerateRequest,
    TaskPlanRetryRequest,
    TaskResponse,
    TaskTimelineResponse,
)
from backend.app.api.schemas.teams import (
    AgentTeamCommandCenterApplyRequest,
    AgentTeamCommandCenterApplyResponse,
    AgentTeamCommandCenterResponse,
    AgentTeamCreateRequest,
    AgentTeamExecutionLoopEnqueueRequest,
    AgentTeamExecutionLoopEnqueueResponse,
    AgentTeamExecutionLoopFinalizeRequest,
    AgentTeamExecutionLoopFinalizeResponse,
    AgentTeamExecutionLoopRunRequest,
    AgentTeamExecutionLoopRunResponse,
    AgentTeamExecutionOverviewResponse,
    AgentTeamMemberCreateRequest,
    AgentTeamMemberModelProviderUpdateRequest,
    AgentTeamMemberResponse,
    AgentTeamMemberUpdateRequest,
    AgentTeamOperationsConsoleResponse,
    AgentTeamOperatorActionRequest,
    AgentTeamOperatorActionResponse,
    AgentTeamOrgChartResponse,
    AgentTeamResponse,
    AgentTeamRuntimeBindRequest,
    AgentTeamRuntimeControlRequest,
    AgentTeamRuntimeEnsureRequest,
    AgentTeamRuntimeResponse,
)
from backend.app.api.services.resources import WorkspaceResourceService
from backend.app.audit.service import AuditService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.errors import PermissionDeniedError
from backend.app.auth.permissions import WorkspaceAction
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.core.trace_context import current_trace_metadata
from backend.app.db.session import get_db_session
from backend.app.model_providers.model_api import configured_model_api
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.diagnostics import ProjectPlanDiagnosticsService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeLimits
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.tasks.correction_diagnostics import TaskCorrectionDiagnosticsService
from backend.app.tasks.corrections import TaskCorrectionService
from backend.app.tasks.events import RedisTaskEventBus, TaskEvent, TaskEventBus
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.live_status import TaskLiveStatusService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.observation import TaskObservationService
from backend.app.tasks.operator_actions import TaskOperatorActionService
from backend.app.tasks.timeline import TaskTimelineService
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_loop import (
    TeamExecutionLoopService,
    enqueue_team_execution_loop_job,
)
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.operations_console import TeamOperationsConsoleService
from backend.app.teams.operator_actions import TeamOperatorActionService
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])
STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def get_task_event_bus(
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> TaskEventBus:
    return RedisTaskEventBus(redis=redis, key_prefix=settings.redis_key_prefix)


def _team_runtime_limits(request: AgentTeamRuntimeEnsureRequest) -> RuntimeLimits | None:
    if request.limits is None:
        return None
    return RuntimeLimits(
        cpu_count=request.limits.cpu_count,
        memory_mb=request.limits.memory_mb,
        disk_mb=request.limits.disk_mb,
        timeout_seconds=request.limits.timeout_seconds,
        max_output_bytes=request.limits.max_output_bytes,
        max_processes=request.limits.max_processes,
    )


@router.get("/agents", response_model=PageResponse[AgentProfileResponse])
async def list_agents(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileResponse]:
    items, total = AgentManagementService(session).list_agents(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(
        items=[agent_profile_response(session, item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/agents/{agent_id}", response_model=AgentProfileResponse)
async def get_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    agent = AgentManagementService(session).get_agent(context.workspace.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: AgentProfileCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentProfileResponse:
    agent_service = AgentManagementService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        agent = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="agents.create",
            idempotency_key=idempotency_key,
            get_existing=lambda agent_id: agent_service.get_agent(
                context.workspace.id,
                agent_id,
            ),
            create=lambda: agent_service.create_agent(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_agent: created_agent.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    return agent_profile_response(session, agent)


@router.patch("/agents/{agent_id}", response_model=AgentProfileResponse)
async def update_agent(
    agent_id: UUID,
    request: AgentProfileUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).update_agent(
            context.workspace.id,
            agent_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents/{agent_id}/archive", response_model=AgentProfileResponse)
async def archive_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).archive_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents/{agent_id}/activate", response_model=AgentProfileResponse)
async def activate_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).activate_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        deleted = AgentManagementService(session).delete_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if deleted is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/agents/{agent_id}/clone",
    response_model=AgentProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_agent(
    agent_id: UUID,
    request: AgentProfileCloneRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentProfileResponse:
    agent_service = AgentManagementService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        agent = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation=f"agents.{agent_id}.clone",
            idempotency_key=idempotency_key,
            get_existing=lambda cloned_agent_id: agent_service.get_agent(
                context.workspace.id,
                cloned_agent_id,
            ),
            create=lambda: agent_service.clone_agent(
                context.workspace.id,
                agent_id,
                request or AgentProfileCloneRequest(),
                context.user.user_id,
            ),
            resource_id=lambda created_agent: created_agent.id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    return agent_profile_response(session, agent)


@router.get(
    "/agents/{agent_id}/versions",
    response_model=PageResponse[AgentProfileVersionResponse],
)
async def list_agent_versions(
    agent_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileVersionResponse]:
    try:
        items, total = AgentManagementService(session).list_agent_versions(
            context.workspace.id,
            agent_id,
            page,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/agents/{agent_id}/versions/{version}/rollback",
    response_model=AgentProfileResponse,
)
async def rollback_agent_version(
    agent_id: UUID,
    version: int = Path(ge=1),
    request: AgentProfileRollbackRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).rollback_agent_version(
            context.workspace.id,
            agent_id,
            version,
            request or AgentProfileRollbackRequest(),
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.get(
    "/agents/{agent_id}/sessions",
    response_model=PageResponse[AgentSessionSummaryResponse],
)
async def list_agent_sessions(
    agent_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    task_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentSessionSummaryResponse]:
    if AgentManagementService(session).get_agent(context.workspace.id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    service = PersistentAgentSessionManagementService(session)
    try:
        total = service.count_sessions(
            workspace_id=context.workspace.id,
            agent_profile_id=agent_id,
            agent_team_id=team_id,
            task_id=task_id,
            status=status_filter,
        )
        items = service.list_sessions(
            workspace_id=context.workspace.id,
            agent_profile_id=agent_id,
            agent_team_id=team_id,
            task_id=task_id,
            status=status_filter,
            limit=page.limit,
            offset=page.offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/agents/{agent_id}/sessions/{session_id}",
    response_model=AgentSessionDetailResponse,
)
async def get_agent_session(
    agent_id: UUID,
    session_id: UUID,
    item_limit: int = Query(default=50, ge=1, le=500),
    item_offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentSessionDetailResponse:
    if AgentManagementService(session).get_agent(context.workspace.id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    detail = PersistentAgentSessionManagementService(session).get_session(
        workspace_id=context.workspace.id,
        session_id=session_id,
        item_limit=item_limit,
        item_offset=item_offset,
    )
    if detail is None or detail.session.agent_profile_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    return AgentSessionDetailResponse.model_validate(detail)


@router.post(
    "/agents/{agent_id}/sessions/{session_id}/archive",
    response_model=AgentSessionSummaryResponse,
)
async def archive_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="archive",
    )


@router.post(
    "/agents/{agent_id}/sessions/{session_id}/freeze",
    response_model=AgentSessionSummaryResponse,
)
async def freeze_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="freeze",
    )


@router.post(
    "/agents/{agent_id}/sessions/{session_id}/activate",
    response_model=AgentSessionSummaryResponse,
)
async def activate_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="activate",
    )


@router.delete(
    "/agents/{agent_id}/sessions/{session_id}/items",
    response_model=AgentSessionClearResponse,
)
async def clear_agent_session_items(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionClearResponse:
    _require_agent_session(session, context.workspace.id, agent_id, session_id)
    deleted = PersistentAgentSessionManagementService(session).clear_session_items(
        workspace_id=context.workspace.id,
        session_id=session_id,
    )
    session.commit()
    return AgentSessionClearResponse(deleted_item_count=deleted)


@router.post(
    "/agents/{agent_id}/sessions/{session_id}/compact",
    response_model=AgentSessionCompactionResponse,
)
async def compact_agent_session(
    agent_id: UUID,
    session_id: UUID,
    request: AgentSessionCompactRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionCompactionResponse:
    _require_agent_session(session, context.workspace.id, agent_id, session_id)
    try:
        result = PersistentAgentSessionManagementService(session).compact_session(
            workspace_id=context.workspace.id,
            session_id=session_id,
            fold_first_n=request.fold_first_n,
            keep_recent_m=request.keep_recent_m,
            summary_role=request.summary_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    session.commit()
    return AgentSessionCompactionResponse.model_validate(result)


def _agent_management_http_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lower_message = message.lower()
    if "not found" in lower_message:
        code = status.HTTP_404_NOT_FOUND
    elif "conflict" in lower_message:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


def _require_agent_session(
    db_session: Session,
    workspace_id: UUID,
    agent_id: UUID,
    session_id: UUID,
) -> None:
    if AgentManagementService(db_session).get_agent(workspace_id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    detail = PersistentAgentSessionManagementService(db_session).get_session(
        workspace_id=workspace_id,
        session_id=session_id,
        item_limit=1,
    )
    if detail is None or detail.session.agent_profile_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")


def _require_team(
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
) -> None:
    if WorkspaceResourceService(db_session).get_team(workspace_id, team_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")


def _require_team_session(
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
    session_id: UUID,
) -> None:
    _require_team(db_session, workspace_id, team_id)
    detail = PersistentAgentSessionManagementService(db_session).get_session(
        workspace_id=workspace_id,
        session_id=session_id,
        item_limit=1,
    )
    if detail is None or detail.session.agent_team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")


def _set_agent_session_status_response(
    *,
    db_session: Session,
    workspace_id: UUID,
    agent_id: UUID,
    session_id: UUID,
    action: str,
) -> AgentSessionSummaryResponse:
    _require_agent_session(db_session, workspace_id, agent_id, session_id)
    service = PersistentAgentSessionManagementService(db_session)
    if action == "archive":
        summary = service.archive_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "freeze":
        summary = service.freeze_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "activate":
        summary = service.activate_session(workspace_id=workspace_id, session_id=session_id)
    else:
        raise ValueError(f"Unsupported session action: {action}")
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    db_session.commit()
    return AgentSessionSummaryResponse.model_validate(summary)


def _set_team_session_status_response(
    *,
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
    session_id: UUID,
    action: str,
) -> AgentSessionSummaryResponse:
    _require_team_session(db_session, workspace_id, team_id, session_id)
    service = PersistentAgentSessionManagementService(db_session)
    if action == "archive":
        summary = service.archive_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "freeze":
        summary = service.freeze_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "activate":
        summary = service.activate_session(workspace_id=workspace_id, session_id=session_id)
    else:
        raise ValueError(f"Unsupported session action: {action}")
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")
    db_session.commit()
    return AgentSessionSummaryResponse.model_validate(summary)


def _append_team_model_provider_updated_message(
    *,
    session: Session,
    workspace_id: UUID,
    team_id: UUID,
    team_member_id: UUID,
    agent_profile_id: UUID,
    actor_user_id: UUID,
    changed: bool,
    reset_session: bool,
    reset_session_count: int,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    thread = TeamRuntimeService(session).ensure_thread(
        workspace_id=workspace_id,
        team_id=team_id,
    )
    if thread is None:
        return
    message = AgentMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        agent_team_id=team_id,
        sender_agent_profile_id=agent_profile_id,
        recipient_agent_profile_id=agent_profile_id,
        message_type="team.runtime.model_provider.updated",
        body=(
            "Team member model provider updated"
            if changed
            else "Team member model provider update reviewed without changes"
        ),
        payload={
            "team_id": str(team_id),
            "team_member_id": str(team_member_id),
            "agent_profile_id": str(agent_profile_id),
            "actor_user_id": str(actor_user_id),
            "changed": changed,
            "reset_session": reset_session,
            "reset_session_count": reset_session_count,
            "before": before,
            "after": after,
        },
        status="sent",
    )
    session.add(message)
    session.flush([message])


def _team_member_model_provider_state(agent) -> dict[str, object]:
    return {
        "model": agent.model,
        "model_provider_credential_id": str(agent.model_provider_credential_id)
        if agent.model_provider_credential_id is not None
        else None,
        "model_api": configured_model_api(agent.model_settings or {}),
    }


@router.get("/teams", response_model=PageResponse[AgentTeamResponse])
async def list_teams(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamResponse]:
    items, total = WorkspaceResourceService(session).list_teams(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/teams", response_model=AgentTeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    request: AgentTeamCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        team = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="teams.create",
            idempotency_key=idempotency_key,
            get_existing=lambda team_id: resource_service.get_team(context.workspace.id, team_id),
            create=lambda: resource_service.create_team(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_team: created_team.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return AgentTeamResponse.model_validate(team)


@router.get("/teams/{team_id}/org-chart", response_model=AgentTeamOrgChartResponse)
async def get_team_org_chart(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOrgChartResponse:
    org_chart = WorkspaceResourceService(session).get_team_org_chart(
        context.workspace.id,
        team_id,
    )
    if org_chart is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOrgChartResponse.model_validate(org_chart)


@router.get(
    "/teams/{team_id}/execution-overview",
    response_model=AgentTeamExecutionOverviewResponse,
)
async def get_team_execution_overview(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamExecutionOverviewResponse:
    overview = TeamExecutionOverviewService(session).get_overview(
        workspace_id=context.workspace.id,
        team_id=team_id,
        include_completed=include_completed,
    )
    if overview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionOverviewResponse.model_validate(overview)


@router.get(
    "/teams/{team_id}/command-center",
    response_model=AgentTeamCommandCenterResponse,
)
async def get_team_command_center(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    queue_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamCommandCenterResponse:
    command_center = TeamCommandCenterService(session).get_command_center(
        workspace_id=context.workspace.id,
        team_id=team_id,
        include_completed=include_completed,
        queue_limit=queue_limit,
    )
    if command_center is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterResponse.model_validate(command_center)


@router.get(
    "/teams/{team_id}/operations-console",
    response_model=AgentTeamOperationsConsoleResponse,
)
async def get_team_operations_console(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    queue_limit: int = Query(default=50, ge=1, le=200),
    message_limit: int = Query(default=10, ge=0, le=50),
    session_limit: int = Query(default=100, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentTeamOperationsConsoleResponse:
    console = TeamOperationsConsoleService(session).get_console(
        workspace_id=context.workspace.id,
        team_id=team_id,
        queue=queue,
        include_completed=include_completed,
        queue_limit=queue_limit,
        message_limit=message_limit,
        session_limit=session_limit,
    )
    if console is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOperationsConsoleResponse.model_validate(console)


@router.get(
    "/teams/{team_id}/sessions",
    response_model=PageResponse[AgentSessionSummaryResponse],
)
async def list_team_sessions(
    team_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentSessionSummaryResponse]:
    _require_team(session, context.workspace.id, team_id)
    service = PersistentAgentSessionManagementService(session)
    try:
        total = service.count_sessions(
            workspace_id=context.workspace.id,
            agent_team_id=team_id,
            status=status_filter,
        )
        items = service.list_sessions(
            workspace_id=context.workspace.id,
            agent_team_id=team_id,
            status=status_filter,
            limit=page.limit,
            offset=page.offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/teams/{team_id}/sessions/{session_id}",
    response_model=AgentSessionDetailResponse,
)
async def get_team_session(
    team_id: UUID,
    session_id: UUID,
    item_limit: int = Query(default=50, ge=1, le=500),
    item_offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentSessionDetailResponse:
    _require_team(session, context.workspace.id, team_id)
    detail = PersistentAgentSessionManagementService(session).get_session(
        workspace_id=context.workspace.id,
        session_id=session_id,
        item_limit=item_limit,
        item_offset=item_offset,
    )
    if detail is None or detail.session.agent_team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")
    return AgentSessionDetailResponse.model_validate(detail)


@router.post(
    "/teams/{team_id}/sessions/{session_id}/archive",
    response_model=AgentSessionSummaryResponse,
)
async def archive_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="archive",
    )


@router.post(
    "/teams/{team_id}/sessions/{session_id}/freeze",
    response_model=AgentSessionSummaryResponse,
)
async def freeze_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="freeze",
    )


@router.post(
    "/teams/{team_id}/sessions/{session_id}/activate",
    response_model=AgentSessionSummaryResponse,
)
async def activate_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="activate",
    )


@router.delete(
    "/teams/{team_id}/sessions/{session_id}/items",
    response_model=AgentSessionClearResponse,
)
async def clear_team_session_items(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionClearResponse:
    _require_team_session(session, context.workspace.id, team_id, session_id)
    deleted = PersistentAgentSessionManagementService(session).clear_session_items(
        workspace_id=context.workspace.id,
        session_id=session_id,
    )
    session.commit()
    return AgentSessionClearResponse(deleted_item_count=deleted)


@router.post(
    "/teams/{team_id}/sessions/{session_id}/compact",
    response_model=AgentSessionCompactionResponse,
)
async def compact_team_session(
    team_id: UUID,
    session_id: UUID,
    request: AgentSessionCompactRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionCompactionResponse:
    _require_team_session(session, context.workspace.id, team_id, session_id)
    try:
        result = PersistentAgentSessionManagementService(session).compact_session(
            workspace_id=context.workspace.id,
            session_id=session_id,
            fold_first_n=request.fold_first_n,
            keep_recent_m=request.keep_recent_m,
            summary_role=request.summary_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")
    session.commit()
    return AgentSessionCompactionResponse.model_validate(result)


@router.get("/teams/{team_id}/runtime", response_model=AgentTeamRuntimeResponse)
async def get_team_runtime(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    state = TeamRuntimeService(session).get_state(
        workspace_id=context.workspace.id,
        team_id=team_id,
    )
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/start", response_model=AgentTeamRuntimeResponse)
async def start_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).start(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/pause", response_model=AgentTeamRuntimeResponse)
async def pause_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).pause(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/resume", response_model=AgentTeamRuntimeResponse)
async def resume_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).resume(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/stop", response_model=AgentTeamRuntimeResponse)
async def stop_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).stop(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/bind", response_model=AgentTeamRuntimeResponse)
async def bind_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeBindRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).bind_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            workspace_runtime_id=request.workspace_runtime_id,
            actor_user_id=context.user.user_id,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/ensure", response_model=AgentTeamRuntimeResponse)
async def ensure_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeEnsureRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).ensure_workspace_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            template_id=request.template_id,
            name=request.name,
            limits=_team_runtime_limits(request),
            network_disabled=request.network_disabled,
            start=request.start,
            reason=request.reason,
            metadata=request.metadata,
        )
    except RuntimeSafetyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except RuntimeQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/continue", response_model=AgentTeamRuntimeResponse)
async def continue_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).continue_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=RuntimeControlService(session, docker, settings),
            instruction=request.instruction or request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post(
    "/teams/{team_id}/command-center/actions/apply",
    response_model=AgentTeamCommandCenterApplyResponse,
)
async def apply_team_command_center_actions(
    team_id: UUID,
    request: AgentTeamCommandCenterApplyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamCommandCenterApplyResponse:
    _require_command_center_runtime_permission(
        session=session,
        context=context,
        request=request,
    )
    response = TeamCommandCenterService(session).apply_action_plan(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        include_completed=request.include_completed,
        queue_limit=request.queue_limit,
        dry_run=request.dry_run,
        sources=request.sources or None,
        actions=request.actions or None,
        max_actions=request.max_actions,
        max_tasks_per_action=request.max_tasks_per_action,
        enqueue_runs=request.enqueue_runs,
        queue=queue,
        runtime_control=None
        if request.dry_run
        else RuntimeControlService(session, docker, settings),
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterApplyResponse.model_validate(response)


def _require_command_center_runtime_permission(
    *,
    session: Session,
    context: WorkspaceContext,
    request: AgentTeamCommandCenterApplyRequest,
) -> None:
    if request.dry_run:
        return
    requested_sources = set(request.sources or [])
    if requested_sources and "team_runtime" not in requested_sources:
        return
    requested_actions = set(request.actions or [])
    if requested_actions and not requested_actions.intersection(
        {"ensure_team_runtime", "start_team_runtime"}
    ):
        return
    try:
        AuthorizationService(session).require_workspace(
            user_id=context.user.user_id,
            workspace_id=context.workspace.id,
            action=WorkspaceAction.MANAGE_RUNTIME,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not allow runtime lifecycle actions",
        ) from exc


@router.post(
    "/teams/{team_id}/execution-loop/enqueue",
    response_model=AgentTeamExecutionLoopEnqueueResponse,
)
async def enqueue_team_execution_loop(
    team_id: UUID,
    request: AgentTeamExecutionLoopEnqueueRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentTeamExecutionLoopEnqueueResponse:
    team = WorkspaceResourceService(session).get_team(context.workspace.id, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    queued = enqueue_team_execution_loop_job(
        queue=queue,
        workspace_id=context.workspace.id,
        team_id=team_id,
        requested_by_user_id=context.user.user_id,
        idempotency_suffix=request.idempotency_suffix,
        priority=request.priority,
        routing={
            "source": "workspace_api",
            "trigger": "manual_enqueue",
            "reason": request.reason,
            "metadata": redact_sensitive_payload(request.metadata),
        },
    )
    return AgentTeamExecutionLoopEnqueueResponse(
        workspace_id=context.workspace.id,
        team_id=team_id,
        status="queued" if queued else "skipped",
        queued=queued,
        job_type=JobType.TEAM_EXECUTION_LOOP.value,
        queue_name=queue.queue_name,
    )


@router.post(
    "/teams/{team_id}/execution-loop/run",
    response_model=AgentTeamExecutionLoopRunResponse,
)
async def run_team_execution_loop_iteration(
    team_id: UUID,
    request: AgentTeamExecutionLoopRunRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    docker: DockerRuntimeClient = Depends(get_docker_runtime_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamExecutionLoopRunResponse:
    _require_execution_loop_runtime_permission(
        session=session,
        context=context,
        request=request,
    )
    response = TeamExecutionLoopService(session).run_iteration(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=request.dry_run,
        apply_command_center_actions=request.apply_command_center_actions,
        enqueue_runs=request.enqueue_runs,
        finalize_ready_tasks=request.finalize_ready_tasks,
        include_completed=request.include_completed,
        queue_limit=request.queue_limit,
        sources=request.sources or None,
        actions=request.actions or None,
        max_actions=request.max_actions,
        max_tasks_per_action=request.max_tasks_per_action,
        max_finalize_tasks=request.max_finalize_tasks,
        queue=queue,
        runtime_control=None
        if request.dry_run
        else RuntimeControlService(session, docker, settings),
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionLoopRunResponse.model_validate(response)


def _require_execution_loop_runtime_permission(
    *,
    session: Session,
    context: WorkspaceContext,
    request: AgentTeamExecutionLoopRunRequest,
) -> None:
    if request.dry_run:
        return
    try:
        AuthorizationService(session).require_workspace(
            user_id=context.user.user_id,
            workspace_id=context.workspace.id,
            action=WorkspaceAction.MANAGE_RUNTIME,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not allow runtime lifecycle actions",
        ) from exc


@router.post(
    "/teams/{team_id}/execution-loop/finalize",
    response_model=AgentTeamExecutionLoopFinalizeResponse,
)
async def finalize_team_execution_loop_tasks(
    team_id: UUID,
    request: AgentTeamExecutionLoopFinalizeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamExecutionLoopFinalizeResponse:
    response = TeamExecutionLoopService(session).finalize_ready_tasks(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=request.dry_run,
        max_tasks=request.max_tasks,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionLoopFinalizeResponse.model_validate(response)


@router.post(
    "/teams/{team_id}/operator-actions",
    response_model=AgentTeamOperatorActionResponse,
)
async def apply_team_operator_action(
    team_id: UUID,
    request: AgentTeamOperatorActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOperatorActionResponse:
    try:
        response = TeamOperatorActionService(session).apply_action(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            action=request.action,
            task_ids=request.task_ids,
            task_step_ids=request.task_step_ids,
            agent_profile_id=request.agent_profile_id,
            max_tasks=request.max_tasks,
            instruction=request.instruction,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOperatorActionResponse.model_validate(response)


@router.get("/teams/{team_id}/members", response_model=PageResponse[AgentTeamMemberResponse])
async def list_team_members(
    team_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamMemberResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_team_members(
            context.workspace.id,
            team_id,
            page,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/teams/{team_id}/members",
    response_model=AgentTeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team_member(
    team_id: UUID,
    request: AgentTeamMemberCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamMemberResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        member = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation=f"teams.{team_id}.members.create",
            idempotency_key=idempotency_key,
            get_existing=lambda member_id: resource_service.get_team_member(
                context.workspace.id,
                team_id,
                member_id,
            ),
            create=lambda: resource_service.create_team_member(
                context.workspace.id,
                team_id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_member: created_member.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return AgentTeamMemberResponse.model_validate(member)


@router.patch(
    "/teams/{team_id}/members/{member_id}",
    response_model=AgentTeamMemberResponse,
)
async def update_team_member(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamMemberResponse:
    try:
        member = WorkspaceResourceService(session, settings).update_team_member(
            context.workspace.id,
            team_id,
            member_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return AgentTeamMemberResponse.model_validate(member)


@router.post(
    "/teams/{team_id}/members/{member_id}/model-provider",
    response_model=AgentProfileResponse,
)
async def update_team_member_model_provider(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberModelProviderUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    member = WorkspaceResourceService(session).get_team_member(
        context.workspace.id,
        team_id,
        member_id,
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found")
    before_agent = AgentManagementService(session).get_agent(
        context.workspace.id,
        member.agent_profile_id,
    )
    if before_agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    before_provider_state = _team_member_model_provider_state(before_agent)
    request_fields = request.model_fields_set
    changes: dict[str, object] = {}
    if "model_provider_credential_id" in request_fields:
        changes["model_provider_credential_id"] = request.model_provider_credential_id
    if request.model is not None:
        changes["model"] = request.model
    if "model_api" in request_fields:
        changes["model_api"] = request.model_api
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide model_provider_credential_id, model, or model_api to update",
        )
    try:
        agent = AgentManagementService(session).update_agent(
            context.workspace.id,
            member.agent_profile_id,
            changes,
            context.user.user_id,
        )
    except ValueError as exc:
        raise _agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    reset_sessions = []
    after_provider_state = _team_member_model_provider_state(agent)
    provider_changed = before_provider_state != after_provider_state
    if request.reset_session and provider_changed:
        reset_sessions = PersistentAgentSessionManagementService(
            session
        ).reset_team_agent_sessions(
            workspace_id=context.workspace.id,
            team_id=team_id,
            agent_profile_id=agent.id,
            reason="team_member_model_provider_updated",
            metadata={
                "team_member_id": str(member.id),
                "before": before_provider_state,
                "after": after_provider_state,
            },
        )
        session.commit()
        session.refresh(agent)
        after_provider_state = _team_member_model_provider_state(agent)
    AuditService(session).record_user_action(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        action="team.member_model_provider.updated",
        target_type="agent_team_member",
        target_id=member.id,
        metadata={
            "team_id": str(team_id),
            "agent_profile_id": str(agent.id),
            "changed": provider_changed,
            "reset_session": request.reset_session,
            "reset_session_count": len(reset_sessions),
            "before": before_provider_state,
            "after": after_provider_state,
        },
    )
    _append_team_model_provider_updated_message(
        session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        team_member_id=member.id,
        agent_profile_id=agent.id,
        actor_user_id=context.user.user_id,
        changed=provider_changed,
        reset_session=request.reset_session,
        reset_session_count=len(reset_sessions),
        before=before_provider_state,
        after=after_provider_state,
    )
    session.commit()
    session.refresh(agent)
    response = agent_profile_response(session, agent)
    if reset_sessions:
        response.model_provider["session_reset"] = {
            "reset_session_count": len(reset_sessions),
            "session_ids": [str(item.id) for item in reset_sessions],
            "reason": "team_member_model_provider_updated",
        }
    return response


@router.get("/tasks", response_model=PageResponse[TaskResponse])
async def list_tasks(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskResponse]:
    items, total = WorkspaceResourceService(session).list_tasks(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: TaskCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    )
    try:
        task = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="tasks.create",
            idempotency_key=idempotency_key,
            get_existing=lambda task_id: resource_service.get_task(context.workspace.id, task_id),
            create=lambda: resource_service.create_task(
                workspace_id=context.workspace.id,
                created_by_user_id=context.user.user_id,
                data=request,
                queue=queue,
            ),
            resource_id=lambda created_task: created_task.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return TaskResponse.model_validate(task)


@router.get("/tasks/manager-queue", response_model=TaskManagerQueueResponse)
async def list_task_manager_queue(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    include_healthy: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskManagerQueueResponse:
    try:
        response = TaskManagerDiagnosticsService(session).list_manager_queue(
            workspace_id=context.workspace.id,
            limit=page.limit,
            offset=page.offset,
            status=status_filter,
            team_id=team_id,
            include_healthy=include_healthy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskManagerQueueResponse.model_validate(response)


@router.get("/tasks/handoff-queue", response_model=TaskHandoffQueueResponse)
async def list_task_handoff_queue(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    handoff_status: str | None = Query(default=None),
    include_terminal: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskHandoffQueueResponse:
    try:
        response = TaskExecutionDiagnosticsService(session).list_handoff_queue(
            workspace_id=context.workspace.id,
            limit=page.limit,
            offset=page.offset,
            task_status=status_filter,
            team_id=team_id,
            handoff_status=handoff_status,
            include_terminal=include_terminal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskHandoffQueueResponse.model_validate(response)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    try:
        task = RunOrchestrationService(session).cancel_task(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/tasks/{task_id}/plan/retry", response_model=TaskResponse)
async def retry_task_plan(
    task_id: UUID,
    request: TaskPlanRetryRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    try:
        task = WorkspaceResourceService(session, settings).retry_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            data=request,
            enqueue_run=request.enqueue and queue is not None,
            queue=queue,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/tasks/{task_id}/plan/regenerate", response_model=TaskResponse)
async def regenerate_task_plan(
    task_id: UUID,
    request: TaskPlanRegenerateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    try:
        task = WorkspaceResourceService(session, settings).regenerate_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            data=request,
            enqueue_run=request.enqueue and queue is not None,
            queue=queue,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.get("/tasks/{task_id}/plan/diagnostics", response_model=TaskPlanDiagnosticsResponse)
async def get_task_plan_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskPlanDiagnosticsResponse:
    diagnostics = ProjectPlanDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskPlanDiagnosticsResponse.model_validate(diagnostics)


@router.get("/tasks/{task_id}/messages", response_model=PageResponse[TaskMessageResponse])
async def list_task_messages(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    message_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskMessageResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_task_messages(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            message_type=message_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/tasks/{task_id}/live-status", response_model=TaskLiveStatusResponse)
async def get_task_live_status(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    message_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskLiveStatusResponse:
    status_snapshot = TaskLiveStatusService(session).get_status(
        workspace_id=context.workspace.id,
        task_id=task_id,
        after_sequence=after_sequence,
        message_limit=message_limit,
    )
    if status_snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskLiveStatusResponse.model_validate(status_snapshot)


@router.get("/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    event_cursor: str = Query(default="$", min_length=1, max_length=64),
    message_limit: int = Query(default=50, ge=1, le=200),
    poll_seconds: float = Query(default=1.0, ge=0.25, le=10.0),
    heartbeat_seconds: float = Query(default=15.0, ge=1.0, le=60.0),
    once: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    task_event_bus: TaskEventBus = Depends(get_task_event_bus),
) -> StreamingResponse:
    service = TaskLiveStatusService(session)
    initial = service.get_status(
        workspace_id=context.workspace.id,
        task_id=task_id,
        after_sequence=after_sequence,
        message_limit=message_limit,
    )
    if initial is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    stream_trace_metadata = current_trace_metadata()

    async def event_stream() -> AsyncIterator[str]:
        cursor = after_sequence
        bus_cursor = event_cursor
        last_emit_at = asyncio.get_running_loop().time()
        snapshot = initial
        while True:
            messages = _snapshot_messages(snapshot)
            if messages:
                cursor = max(_message_sequence(message, cursor) for message in messages)
            yield _sse_event(
                "task.snapshot",
                {
                    **snapshot,
                    "stream": {
                        "cursor": cursor,
                        "event_cursor": bus_cursor,
                        "message_count": len(messages),
                        "complete": _task_stream_complete(snapshot),
                    },
                },
            )
            last_emit_at = asyncio.get_running_loop().time()

            complete = _task_stream_complete(snapshot)
            stop_after_bus_drain = once or complete

            bus_events = await _read_task_bus_events(
                task_event_bus,
                workspace_id=context.workspace.id,
                task_id=task_id,
                after_id=bus_cursor,
                wait_seconds=0 if stop_after_bus_drain else poll_seconds,
            )
            for task_event in bus_events:
                bus_cursor = task_event.id
                yield _sse_event("task.event", _task_event_stream_payload(task_event, cursor))
                last_emit_at = asyncio.get_running_loop().time()

            if stop_after_bus_drain:
                break

            session.expire_all()
            next_snapshot = service.get_status(
                workspace_id=context.workspace.id,
                task_id=task_id,
                after_sequence=cursor,
                message_limit=message_limit,
            )
            if next_snapshot is None:
                yield _sse_event("task.missing", {"task_id": str(task_id), "cursor": cursor})
                break
            snapshot = next_snapshot
            if not _snapshot_messages(snapshot):
                now = asyncio.get_running_loop().time()
                if now - last_emit_at >= heartbeat_seconds:
                    yield _sse_event(
                        "heartbeat",
                        {
                            "workspace_id": str(context.workspace.id),
                            "task_id": str(task_id),
                            "cursor": cursor,
                            "event_cursor": bus_cursor,
                            **stream_trace_metadata,
                        },
                    )
                    last_emit_at = now

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get(
    "/tasks/{task_id}/planning-attempts",
    response_model=PageResponse[TaskPlanningAttemptResponse],
)
async def list_task_planning_attempts(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskPlanningAttemptResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_task_planning_attempts(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(
        items=[TaskPlanningAttemptResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/tasks/{task_id}/corrections",
    response_model=TaskCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_correction(
    task_id: UUID,
    request: TaskCorrectionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskCorrectionResponse:
    try:
        correction = TaskCorrectionService(session).create_correction(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if correction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskCorrectionResponse(**correction.__dict__)


@router.get(
    "/tasks/{task_id}/corrections/diagnostics",
    response_model=TaskCorrectionDiagnosticsResponse,
)
async def get_task_correction_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskCorrectionDiagnosticsResponse:
    diagnostics = TaskCorrectionDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskCorrectionDiagnosticsResponse.model_validate(diagnostics)


@router.get("/tasks/{task_id}/observation", response_model=TaskObservationResponse)
async def get_task_observation(
    task_id: UUID,
    view_type: str | None = Query(default="auto"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskObservationResponse:
    try:
        observation = TaskObservationService(session).get_observation(
            workspace_id=context.workspace.id,
            task_id=task_id,
            view_type=view_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskObservationResponse.model_validate(observation)


@router.get("/tasks/{task_id}/timeline", response_model=TaskTimelineResponse)
async def get_task_timeline(
    task_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskTimelineResponse:
    timeline = TaskTimelineService(session).get_timeline(
        workspace_id=context.workspace.id,
        task_id=task_id,
        limit=limit,
    )
    if timeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskTimelineResponse.model_validate(timeline)


@router.post("/tasks/{task_id}/operator-actions", response_model=TaskOperatorActionResponse)
async def apply_task_operator_action(
    task_id: UUID,
    request: TaskOperatorActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskOperatorActionResponse:
    try:
        response = TaskOperatorActionService(session).apply_action(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            action=request.action,
            task_step_ids=request.task_step_ids,
            agent_profile_id=request.agent_profile_id,
            instruction=request.instruction,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskOperatorActionResponse.model_validate(response)


@router.get(
    "/tasks/{task_id}/execution-diagnostics",
    response_model=TaskExecutionDiagnosticsResponse,
)
async def get_task_execution_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskExecutionDiagnosticsResponse:
    diagnostics = TaskExecutionDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskExecutionDiagnosticsResponse.model_validate(diagnostics)


@router.get(
    "/tasks/{task_id}/manager-diagnostics",
    response_model=TaskManagerDiagnosticsResponse,
)
async def get_task_manager_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskManagerDiagnosticsResponse:
    diagnostics = TaskManagerDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskManagerDiagnosticsResponse.model_validate(diagnostics)


@router.get("/runs", response_model=PageResponse[AgentRunResponse])
async def list_runs(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    items, total = WorkspaceResourceService(session).list_runs(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/runs/{agent_run_id}/events", response_model=PageResponse[RunEventResponse])
async def list_run_events(
    agent_run_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RunEventResponse]:
    items, total = WorkspaceResourceService(session).list_run_events(
        context.workspace.id,
        agent_run_id,
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/runs/{agent_run_id}/cancel", response_model=AgentRunResponse)
async def cancel_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentRunResponse:
    try:
        run = RunOrchestrationService(session).cancel_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.post(
    "/runs/{agent_run_id}/retry",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentRunResponse:
    try:
        run = RunOrchestrationService(session, queue=queue).retry_failed_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.get("/audit-events", response_model=PageResponse[AuditEventResponse])
async def list_audit_events(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = WorkspaceResourceService(session).list_audit_events(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    redacted = redact_sensitive_payload(payload)
    data = json.dumps(jsonable_encoder(redacted), ensure_ascii=False, sort_keys=True)
    return f"event: {event_name}\ndata: {data}\n\n"


def _task_event_stream_payload(task_event: TaskEvent, cursor: int) -> dict[str, object]:
    payload: dict[str, object] = {
        **task_event.as_dict(),
        "event_id": task_event.event_id or task_event.id,
        "stream": {
            "cursor": cursor,
            "event_cursor": task_event.id,
        },
    }
    outbox_id = task_event.outbox_id or task_event.payload.get("outbox_id")
    if isinstance(outbox_id, str):
        payload["outbox_id"] = outbox_id
    return payload


async def _read_task_bus_events(
    task_event_bus: TaskEventBus,
    *,
    workspace_id: UUID,
    task_id: UUID,
    after_id: str,
    wait_seconds: float,
) -> list[TaskEvent]:
    try:
        return await asyncio.to_thread(
            task_event_bus.read,
            workspace_id=workspace_id,
            task_id=task_id,
            after_id=after_id,
            count=10,
            block_ms=int(max(0, wait_seconds) * 1000),
        )
    except RedisError:
        return []


def _snapshot_messages(snapshot: dict[str, object]) -> list[dict[str, object]]:
    messages = snapshot.get("recent_messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def _message_sequence(message: dict[str, object], default: int) -> int:
    sequence = message.get("sequence")
    return sequence if isinstance(sequence, int) else default


def _task_stream_complete(snapshot: dict[str, object]) -> bool:
    task = snapshot.get("task")
    summary = snapshot.get("summary")
    if not isinstance(task, dict) or not isinstance(summary, dict):
        return False
    status_value = task.get("status")
    active_runs = summary.get("active_run_count")
    return status_value in STREAM_TERMINAL_TASK_STATUSES and active_runs == 0
