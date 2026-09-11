from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import TaskStep


class RunProfileLookup:
    def __init__(self, session: Session) -> None:
        self._session = session

    def for_step(self, workspace_id: UUID, step: TaskStep) -> AgentProfile | None:
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        if profile is None or profile.workspace_id != workspace_id:
            return None
        return profile
