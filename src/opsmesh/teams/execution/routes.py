from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.errors import PermissionDeniedError
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.execution.queries import team_orchestration_requests
from opsmesh.teams.execution.reconciliation import (
    TeamOrchestrationRequestNotFoundError,
    TeamReconciliationConflictError,
    TeamReconciliationService,
)
from opsmesh.teams.execution.service import (
    TeamOrchestrationService,
)
from opsmesh.teams.management.http_common import _runtime_control
from opsmesh.teams.operations.command_center import TeamCommandCenterService
from opsmesh.teams.operations.operator_actions import TeamOperatorActionService
from opsmesh.teams.sessions.schemas import (
    AgentTeamCommandCenterApplyRequest,
    AgentTeamCommandCenterApplyResponse,
    AgentTeamOperatorActionRequest,
    AgentTeamOperatorActionResponse,
    AgentTeamOrchestrationAdvanceRequest,
    AgentTeamOrchestrationAdvanceResponse,
    AgentTeamOrchestrationFinalizeRequest,
    AgentTeamOrchestrationFinalizeResponse,
    AgentTeamOrchestrationReconcileRequest,
    AgentTeamOrchestrationSubmitRequest,
    TeamOrchestrationResponse,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.post(
    "/teams/{team_id}/command-center/actions/apply",
    response_model=AgentTeamCommandCenterApplyResponse,
)
def apply_team_command_center_actions(
    team_id: UUID,
    request: AgentTeamCommandCenterApplyRequest,
    context: WorkspaceContext = Depends(
        workspace_dependency(WorkspaceAction.OPERATE, path_resource_action=ResourceAction.READ)
    ),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamCommandCenterApplyResponse:
    _require_command_center_action_permissions(
        session=session,
        context=context,
        team_id=team_id,
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
        runtime_control=None if request.dry_run else _runtime_control(session, settings),
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterApplyResponse.model_validate(response)


def _require_command_center_action_permissions(
    *,
    session: Session,
    context: WorkspaceContext,
    team_id: UUID,
    request: AgentTeamCommandCenterApplyRequest,
) -> None:
    if request.dry_run:
        return
    ResourceAuthorizationService(session, context.user).require(
        context.workspace.id,
        ResourceKind.TEAM,
        team_id,
        ResourceAction.CONTROL,
    )
    requested_sources = set(request.sources or [])
    if requested_sources and "team_runtime" not in requested_sources:
        return
    requested_actions = set(request.actions or [])
    if requested_actions and not requested_actions.intersection(
        {"ensure_team_runtime", "start_team_runtime"}
    ):
        return
    try:
        WorkspaceAuthorizationService(session).require_workspace(
            user_id=context.user.user_id,
            workspace_id=context.workspace.id,
            action=WorkspaceAction.MANAGE_RUNTIME,
            authenticated_user=context.user,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not allow runtime lifecycle actions",
        ) from exc


@router.post(
    "/teams/{team_id}/orchestration/requests",
    response_model=TeamOrchestrationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_team_orchestration_request(
    team_id: UUID,
    request: AgentTeamOrchestrationSubmitRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> TeamOrchestrationResponse:
    from opsmesh.teams.execution.dispatch import submit_team_orchestration

    try:
        command, workflow_id = submit_team_orchestration(
            session,
            workspace_id=context.workspace.id,
            team_id=team_id,
            user_id=context.user.user_id,
            configuration=request,
        )
    except TeamReconciliationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.commit()
    return TeamOrchestrationResponse(
        workspace_id=context.workspace.id,
        team_id=team_id,
        request_id=command.id,
        workflow_id=workflow_id,
        status=command.status,
    )


@router.get(
    "/teams/{team_id}/orchestration/requests",
    response_model=PageResponse[TeamOrchestrationResponse],
)
def list_team_orchestration_requests(
    team_id: UUID,
    request_status: Literal["queued", "running", "completed", "failed", "unknown"] | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    page: PageParams = Depends(pagination_params),
) -> PageResponse[TeamOrchestrationResponse]:
    ResourceAuthorizationService(session, context.user).require(
        context.workspace.id, ResourceKind.TEAM, team_id, ResourceAction.READ
    )
    items, total = team_orchestration_requests(
        session, context.workspace.id, team_id, page, request_status
    )
    return PageResponse(
        items=[_orchestration_request_response(command) for command in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/teams/{team_id}/orchestration/requests/{request_id}",
    response_model=TeamOrchestrationResponse,
)
def get_team_orchestration_request(
    team_id: UUID,
    request_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TeamOrchestrationResponse:
    from sqlalchemy import select

    ResourceAuthorizationService(session, context.user).require(
        context.workspace.id, ResourceKind.TEAM, team_id, ResourceAction.READ
    )
    command = session.scalar(
        select(TeamOrchestrationRequest).where(
            TeamOrchestrationRequest.workspace_id == context.workspace.id,
            TeamOrchestrationRequest.team_id == team_id,
            TeamOrchestrationRequest.id == request_id,
        )
    )
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team request not found")
    return _orchestration_request_response(command)


def _orchestration_request_response(
    command: TeamOrchestrationRequest,
) -> TeamOrchestrationResponse:
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest

    execution = ExecutionRequest(
        execution_id=command.id,
        workspace_id=command.workspace_id,
        resource_id=command.id,
        kind=ExecutionKind.TEAM_ORCHESTRATION,
    )
    return TeamOrchestrationResponse(
        workspace_id=command.workspace_id,
        team_id=command.team_id,
        request_id=command.id,
        workflow_id=execution.workflow_id,
        status=command.status,
        result=command.result,
        last_error_code=command.last_error_code,
        started_at=command.started_at,
        completed_at=command.completed_at,
    )


@router.post(
    "/teams/{team_id}/orchestration/requests/{request_id}/reconcile",
    response_model=TeamOrchestrationResponse,
)
def reconcile_team_orchestration_request(
    team_id: UUID,
    request_id: UUID,
    request: AgentTeamOrchestrationReconcileRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> TeamOrchestrationResponse:
    try:
        TeamReconciliationService(session).reconcile(
            workspace_id=context.workspace.id,
            team_id=team_id,
            request_id=request_id,
            user=context.user,
            decision=request,
        )
    except TeamOrchestrationRequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TeamReconciliationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.commit()
    return get_team_orchestration_request(team_id, request_id, context, session)


@router.post(
    "/teams/{team_id}/orchestration/preview",
    response_model=AgentTeamOrchestrationAdvanceResponse,
)
def preview_team_orchestration(
    team_id: UUID,
    request: AgentTeamOrchestrationAdvanceRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOrchestrationAdvanceResponse:
    response = TeamOrchestrationService(session).advance(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=True,
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
        runtime_control=None,
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOrchestrationAdvanceResponse.model_validate(response)


@router.post(
    "/teams/{team_id}/orchestration/finalize",
    response_model=AgentTeamOrchestrationFinalizeResponse,
)
def finalize_team_tasks(
    team_id: UUID,
    request: AgentTeamOrchestrationFinalizeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOrchestrationFinalizeResponse:
    response = TeamOrchestrationService(session).finalize_ready_tasks(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=request.dry_run,
        max_tasks=request.max_tasks,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOrchestrationFinalizeResponse.model_validate(response)


@router.post(
    "/teams/{team_id}/operator-actions",
    response_model=AgentTeamOperatorActionResponse,
)
def apply_team_operator_action(
    team_id: UUID,
    request: AgentTeamOperatorActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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
