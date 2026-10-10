from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.orchestration.tasks.state import TERMINAL_TASK_STATUSES
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.teams.management.models import AgentTeam
from opsmesh.teams.sessions.service import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
)
from opsmesh.workspaces.management.models import Workspace


class TeamExecutionLoopQueueRepository:
    """Read scheduler inputs for team execution loop maintenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active_team_tasks(self, *, limit: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .join(
                    AgentTeam,
                    (AgentTeam.id == Task.agent_team_id)
                    & (AgentTeam.workspace_id == Task.workspace_id),
                )
                .where(
                    or_(
                        AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY]["status"]
                        .as_string()
                        .is_(None),
                        AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY]["status"]
                        .as_string()
                        .not_in([TEAM_RUNTIME_PAUSED, TEAM_RUNTIME_STOPPED]),
                    ),
                    ~exists(
                        select(AgentRun.id).where(
                            AgentRun.workspace_id == Task.workspace_id,
                            AgentRun.task_id == Task.id,
                            AgentRun.status.in_(
                                [
                                    "queued",
                                    "running",
                                    "waiting_approval",
                                    "waiting_runtime",
                                    "waiting_subworkflow",
                                ]
                            ),
                        )
                    ),
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
