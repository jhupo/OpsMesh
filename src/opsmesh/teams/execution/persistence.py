"""Shared orchestration infrastructure: constants, queueing, persistence and lookup."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.teams.management.models import AgentTeam as _AgentTeamModel
from opsmesh.teams.sessions.service import TeamOperationService

COMPLETED_STEP_STATUSES = {"completed", "cancelled", "skipped"}


class TeamOrchestrationRecorder:
    """Persist explicit orchestration advancements and audit events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_advancement(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        TeamOperationService(self._session).record_advancement(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="team.orchestration.advancement_ran",
            target_type="agent_team",
            target_id=team_id,
            metadata=summary,
        )
        self._session.commit()


class TeamOrchestrationRepository:
    """Read team orchestration prerequisites."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, *, workspace_id: UUID, team_id: UUID) -> _AgentTeamModel | None:
        return self._session.scalar(
            select(_AgentTeamModel).where(
                _AgentTeamModel.workspace_id == workspace_id, _AgentTeamModel.id == team_id
            )
        )

    def team_exists(self, *, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(_AgentTeamModel.id).where(
                    _AgentTeamModel.workspace_id == workspace_id, _AgentTeamModel.id == team_id
                )
            )
            is not None
        )
