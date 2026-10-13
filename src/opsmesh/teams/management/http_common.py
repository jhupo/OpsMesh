from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.agents.sessions.schemas import AgentSessionSummaryResponse
from opsmesh.agents.sessions.service import AgentSessionService
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.shared.config import Settings
from opsmesh.teams.management.service import WorkspaceTeamService


def _runtime_control(session: Session, settings: Settings) -> RuntimeEnvironmentService:
    return RuntimeEnvironmentService(session, settings=settings)


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
    detail = AgentSessionService(db_session).get_session(
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
    service = AgentSessionService(db_session)
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
