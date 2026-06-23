from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.sessions import ACTIVE_SESSION_STATUS, PersistentAgentSession
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime_constants import TEAM_RUNTIME_SESSION_SCOPE
from backend.app.teams.runtime_refs import _member_session_key, _team_session_key


class TeamRuntimeSessionStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def member_sessions(self, team: AgentTeam) -> list[PersistentAgentSession]:
        members = self._active_members(team.workspace_id, team.id)
        sessions: list[PersistentAgentSession] = []
        for member in members:
            sessions.append(
                self._get_or_create_session(
                    workspace_id=team.workspace_id,
                    session_key=_member_session_key(
                        team.workspace_id,
                        team.id,
                        member.agent_profile_id,
                    ),
                    scope_type="team_agent",
                    scope_id=f"{team.id}:{member.agent_profile_id}",
                    agent_profile_id=member.agent_profile_id,
                    agent_team_id=team.id,
                    metadata={
                        "source": "team_runtime",
                        "team_role": member.team_role,
                        "department": member.department,
                    },
                )
            )
        return sessions


    def _get_or_create_team_session(self, team: AgentTeam) -> PersistentAgentSession:
        return self._get_or_create_session(
            workspace_id=team.workspace_id,
            session_key=_team_session_key(team.workspace_id, team.id),
            scope_type=TEAM_RUNTIME_SESSION_SCOPE,
            scope_id=str(team.id),
            agent_profile_id=team.manager_agent_profile_id,
            agent_team_id=team.id,
            metadata={"source": "team_runtime", "team_name": team.name},
        )


    def _existing_team_session(self, team: AgentTeam) -> PersistentAgentSession | None:
        return self._session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == team.workspace_id,
                PersistentAgentSession.session_key == _team_session_key(
                    team.workspace_id,
                    team.id,
                ),
            )
        )


    def _existing_member_sessions(self, team: AgentTeam) -> list[PersistentAgentSession]:
        return list(
            self._session.scalars(
                select(PersistentAgentSession)
                .where(
                    PersistentAgentSession.workspace_id == team.workspace_id,
                    PersistentAgentSession.agent_team_id == team.id,
                    PersistentAgentSession.scope_type == "team_agent",
                )
                .order_by(PersistentAgentSession.updated_at.desc(), PersistentAgentSession.id)
            )
        )


    def _get_or_create_session(
        self,
        *,
        workspace_id: UUID,
        session_key: str,
        scope_type: str,
        scope_id: str,
        agent_profile_id: UUID | None,
        agent_team_id: UUID,
        metadata: dict[str, object],
    ) -> PersistentAgentSession:
        existing = self._session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.session_key == session_key,
            )
        )
        if existing is not None:
            return existing
        created = PersistentAgentSession(
            workspace_id=workspace_id,
            session_key=session_key,
            scope_type=scope_type,
            scope_id=scope_id,
            agent_profile_id=agent_profile_id,
            agent_team_id=agent_team_id,
            session_metadata=metadata,
            status=ACTIVE_SESSION_STATUS,
        )
        self._session.add(created)
        self._session.flush([created])
        return created

    def _active_members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
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
