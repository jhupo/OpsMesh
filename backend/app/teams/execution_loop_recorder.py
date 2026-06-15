from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.teams.runtime import TeamRuntimeService


class TeamExecutionLoopIterationRecorder:
    """Persist execution loop heartbeats and audit events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        TeamRuntimeService(self._session).record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="team.execution_loop.iteration_ran",
            target_type="agent_team",
            target_id=team_id,
            metadata=summary,
        )
        self._session.commit()
