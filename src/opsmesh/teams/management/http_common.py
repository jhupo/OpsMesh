from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.schemas import AgentSessionSummaryResponse
from opsmesh.agents.sessions.management import PersistentAgentSessionManagementService
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.runtime.commands.queued_control import QueuedRuntimeControl
from opsmesh.runtime.instances.contracts import RuntimeLimits
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.config import Settings
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.teams.execution.loop import enqueue_team_execution_loop_job
from opsmesh.teams.management.service import WorkspaceTeamService
from opsmesh.teams.sessions.schemas import (
    AgentTeamRuntimeControlRequest,
    AgentTeamRuntimeEnsureRequest,
)

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


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


def _enqueue_team_runtime_control(
    session: Session,
    queue: RedisQueue,
    context: WorkspaceContext,
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest | AgentTeamRuntimeEnsureRequest,
    action: str,
) -> None:
    enqueue_team_execution_loop_job(
        session=session,
        queue=queue,
        workspace_id=context.workspace.id,
        team_id=team_id,
        requested_by_user_id=context.user.user_id,
        priority=10,
        routing={
            "source": "workspace_api",
            "trigger": f"team_runtime_{action}",
            "reason": request.reason,
            "metadata": redact_sensitive_payload(request.metadata),
            "runtime_action": action,
        },
    )


def _queued_runtime_control(
    session: Session,
    queue: RedisQueue,
    settings: Settings,
    context: WorkspaceContext,
) -> QueuedRuntimeControl:
    return QueuedRuntimeControl(
        session=session,
        queue=queue,
        settings=settings,
        requested_by_user_id=context.user.user_id,
    )


def _require_team(
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
) -> None:
    if WorkspaceTeamService(db_session).get_team(workspace_id, team_id) is None:
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
