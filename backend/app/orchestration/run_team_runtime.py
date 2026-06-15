from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import team_bound_runtime_id


class RunTeamRuntimeResolver:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runtime_id_for_task(self, task: Task) -> UUID | None:
        if task.agent_team_id is None:
            return None
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
            )
        )
        if team is None:
            return None
        return team_bound_runtime_id(team)
