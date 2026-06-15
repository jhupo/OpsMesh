from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.teams.models import AgentTeam


class TeamExecutionLoopRepository:
    """Read team execution loop prerequisites."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, *, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def team_exists(self, *, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                )
            )
            is not None
        )
