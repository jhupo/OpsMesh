from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.runtime_manager.spaces.models import RuntimeSpace
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime_constants import TEAM_RUNTIME_STATUS_KEY
from backend.app.teams.runtime_refs import _uuid_or_none, team_bound_runtime_id


class TeamRuntimeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )


    def runtime(self, workspace_id: UUID, workspace_runtime_id: UUID) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == workspace_runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )


    def bound_runtime(self, team: AgentTeam) -> WorkspaceRuntime | None:
        runtime_id = team_bound_runtime_id(team)
        if runtime_id is None:
            return None
        return self.runtime(team.workspace_id, runtime_id)


    def resolve_template_id(self, team: AgentTeam, template_id: UUID | None) -> UUID | None:
        if template_id is not None:
            return template_id
        policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
        runtime_policy = policy.get(TEAM_RUNTIME_STATUS_KEY)
        if isinstance(runtime_policy, dict):
            policy_template_id = _uuid_or_none(runtime_policy.get("runtime_template_id"))
            if policy_template_id is not None:
                return policy_template_id
        if team.runtime_space_id is not None:
            runtime_space = self._session.scalar(
                select(RuntimeSpace).where(
                    RuntimeSpace.workspace_id == team.workspace_id,
                    RuntimeSpace.id == team.runtime_space_id,
                )
            )
            if runtime_space is not None and runtime_space.default_runtime_template_id is not None:
                return runtime_space.default_runtime_template_id
        return self._session.scalar(
            select(RuntimeTemplate.id)
            .where(RuntimeTemplate.status == "active")
            .order_by(RuntimeTemplate.created_at.desc(), RuntimeTemplate.name.asc())
            .limit(1)
        )


    def active_members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                    AgentTeamMember.status == "active",
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )
