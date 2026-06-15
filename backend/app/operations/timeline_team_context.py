from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.operations.timeline_utils import (
    configured_mcp_tools,
    team_runtime_team_id,
)
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TeamRuntimeTimelineContext:
    def __init__(self, session: Session) -> None:
        self._session = session

    def team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                )
            )
            is not None
        )

    def team_runtime_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        ).all()
        team_id_text = str(team_id)
        return {
            runtime.id
            for runtime in runtimes
            if team_runtime_team_id(runtime.capabilities) == team_id_text
        }

    def team_member_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        return set(
            self._session.scalars(
                select(AgentTeamMember.id).where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
            ).all()
        )

    def team_run_event_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        return set(
            self._session.scalars(
                select(AgentRun.id)
                .join(Task, Task.id == AgentRun.task_id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    Task.workspace_id == workspace_id,
                    Task.agent_team_id == team_id,
                )
            ).all()
        )

    def configured_mcp_tools(self, workspace_id: UUID, team_id: UUID) -> set[str]:
        rows = self._session.scalars(
            select(AgentProfile.tool_policy)
            .join(AgentTeamMember, AgentTeamMember.agent_profile_id == AgentProfile.id)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.status == "active",
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.status == "active",
            )
        ).all()
        configured: set[str] = set()
        for policy in rows:
            configured.update(configured_mcp_tools(policy))
        return configured
