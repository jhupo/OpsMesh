from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.projects.models import AgentRunProjectIOState


class RunProjectIOQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(
        self,
        workspace_id: UUID,
        run_id: UUID,
    ) -> AgentRunProjectIOState | None:
        return self._session.scalar(
            select(AgentRunProjectIOState).where(
                AgentRunProjectIOState.workspace_id == workspace_id,
                AgentRunProjectIOState.agent_run_id == run_id,
            )
        )
