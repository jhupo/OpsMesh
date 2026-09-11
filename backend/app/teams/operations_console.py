from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.agent_runtime.session_views import PersistentSessionSummary
from backend.app.agents.models import AgentProfile
from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUSES
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.operations_console_controls import (
    _controls_payload,
    _readiness_payload,
)
from backend.app.teams.operations_console_mailbox import TeamOperationsMailboxReader
from backend.app.teams.operations_console_payloads import (
    _command_center_payload,
    _session_payload,
    _team_payload,
)
from backend.app.teams.operations_console_provider_summary import (
    _agent_model_provider_payload,
)
from backend.app.teams.operations_console_providers import TeamProviderManagementBuilder
from backend.app.teams.operations_console_runtime_payloads import (
    _runtime_payload,
    _team_loop_queue_payload,
)
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.teams.scheduling_blocks import scheduled_run_blocking_summary
from backend.app.workers.queue.redis_queue import RedisQueue

PROVIDER_RUN_STATUSES = ACTIVE_RUN_STATUSES


class TeamOperationsConsoleService:
    """Operator-facing aggregate for a persistent team/company runtime."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_console(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        queue: RedisQueue | None = None,
        include_completed: bool = False,
        queue_limit: int = 50,
        message_limit: int = 10,
        session_limit: int = 100,
    ) -> dict[str, object] | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None

        runtime_state = TeamRuntimeService(self._session).get_state(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        if runtime_state is None:
            return None

        command_center = TeamCommandCenterService(self._session).get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        sessions = PersistentAgentSessionManagementService(self._session).list_sessions(
            workspace_id=workspace_id,
            agent_team_id=team_id,
            limit=session_limit,
            offset=0,
        )
        members = self._members(team)
        session_by_agent_id = {
            summary.agent_profile_id: summary
            for summary in sessions
            if summary.agent_profile_id is not None
        }
        team_session = next(
            (summary for summary in sessions if summary.id == runtime_state.team_session_id),
            None,
        ) if runtime_state.team_session_id is not None else None
        runtime_queue = _team_loop_queue_payload(
            queue=queue,
            workspace_id=workspace_id,
            team_id=team_id,
            limit=max(queue_limit, 0),
        )
        provider_management = TeamProviderManagementBuilder(self._session).payload(
            workspace_id=workspace_id,
            members=members,
        )
        runtime_blocking = scheduled_run_blocking_summary(
            self._session,
            workspace_id=workspace_id,
            team_id=team_id,
        )
        mailbox = TeamOperationsMailboxReader(self._session).team_payload(
            runtime_state=runtime_state,
            message_limit=max(message_limit, 0),
        )
        controls = _controls_payload(
            runtime_state,
            command_center,
            runtime_queue,
            provider_management,
            runtime_blocking,
        )
        mailbox_reader = TeamOperationsMailboxReader(self._session)
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "team": _team_payload(team),
            "runtime": _runtime_payload(
                runtime_state,
                runtime_queue=runtime_queue,
                runtime_blocking=runtime_blocking,
            ),
            "command_center": _command_center_payload(command_center),
            "members": [
                self._member_payload(
                    member=member,
                    session=session_by_agent_id.get(member.agent_profile_id),
                    mailbox_reader=mailbox_reader,
                )
                for member in members
            ],
            "provider_management": provider_management,
            "sessions": {
                "team_session": _session_payload(team_session),
                "member_sessions": [
                    _session_payload(summary)
                    for summary in sessions
                    if summary.scope_type == "team_agent"
                ],
                "total": len(sessions),
            },
            "mailbox": mailbox,
            "controls": controls,
            "readiness": _readiness_payload(
                runtime_state,
                command_center,
                runtime_queue,
                provider_management,
                runtime_blocking,
                mailbox,
                controls,
            ),
        }

    def _team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def _members(self, team: AgentTeam) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == team.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _member_payload(
        self,
        *,
        member: AgentTeamMember,
        session: PersistentSessionSummary | None,
        mailbox_reader: TeamOperationsMailboxReader,
    ) -> dict[str, object]:
        agent = self._session.get(AgentProfile, member.agent_profile_id)
        return {
            "team_member_id": member.id,
            "agent_profile_id": member.agent_profile_id,
            "agent": _agent_payload(agent),
            "reports_to_member_id": member.reports_to_member_id,
            "team_role": member.team_role,
            "department": member.department,
            "position_title": member.position_title,
            "responsibilities": list(member.responsibilities),
            "accepts_tasks": member.accepts_tasks,
            "max_concurrent_tasks": member.max_concurrent_tasks,
            "status": member.status,
            "session": _session_payload(session),
            "mailbox": mailbox_reader.agent_payload(
                workspace_id=member.workspace_id,
                agent_team_id=member.agent_team_id,
                agent_profile_id=member.agent_profile_id,
            ),
        }

def _agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
        "model": agent.model,
        "model_provider_credential_id": agent.model_provider_credential_id,
        "model_provider": _agent_model_provider_payload(agent),
    }


