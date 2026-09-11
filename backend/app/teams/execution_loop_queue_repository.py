from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.tasks.models import Task
from backend.app.tasks.status import TERMINAL_TASK_STATUSES
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
)
from backend.app.workspaces.models import Workspace


class TeamExecutionLoopQueueRepository:
    """Read scheduler inputs for team execution loop maintenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active_team_tasks(self, *, limit: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(
                    Task.agent_team_id.is_not(None),
                    Task.created_by_user_id.is_not(None),
                    ~Task.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
                )
                .order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
                .limit(max(limit, 1) * 5)
            )
        )

    def runtime_teams(self, *, limit: int) -> list[AgentTeam]:
        runtime_status = AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY][
            "status"
        ].as_string()
        return list(
            self._session.scalars(
                select(AgentTeam)
                .where(
                    AgentTeam.status == "active",
                    or_(
                        runtime_status.in_(
                            [
                                TEAM_RUNTIME_RUNNING,
                                TEAM_RUNTIME_PAUSED,
                                TEAM_RUNTIME_STOPPED,
                            ]
                        ),
                        AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY][
                            "last_iteration"
                        ].is_not(None),
                    ),
                )
                .order_by(AgentTeam.updated_at.desc(), AgentTeam.id.asc())
                .limit(max(limit, 1) * 5)
            )
        )

    def workspace_owner_id(self, workspace_id: UUID) -> UUID | None:
        return self._session.scalar(
            select(Workspace.owner_user_id).where(Workspace.id == workspace_id)
        )

    def workspace_runtime(
        self,
        workspace_id: UUID,
        workspace_runtime_id: UUID,
    ) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == workspace_runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )
