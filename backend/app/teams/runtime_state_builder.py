from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from backend.app.agent_messages.models import AgentMessageThread
from backend.app.agent_runtime.sessions import PersistentAgentSession
from backend.app.teams.models import AgentTeam
from backend.app.teams.operating_context import TeamOperatingContextService
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_refs import _uuid_or_none, team_runtime_metadata
from backend.app.teams.runtime_repository import TeamRuntimeRepository
from backend.app.teams.runtime_state_utils import _last_iteration, _runtime_health


@dataclass(frozen=True)
class TeamRuntimeState:
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    status: str
    team_session_id: UUID | None
    team_session_key: str | None
    thread_id: UUID | None
    workspace_runtime_id: UUID | None
    runtime_status: str | None
    runtime_space_id: UUID | None
    last_iteration: dict[str, object] | None
    last_message_at: datetime | None
    runtime_health: str
    member_session_count: int
    member_agent_ids: list[UUID]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    metadata: dict[str, object]


class TeamRuntimeStateBuilder:
    """Assemble the runtime state snapshot returned to APIs and schedulers."""

    def __init__(
        self,
        *,
        repo: TeamRuntimeRepository,
        mailbox: TeamRuntimeMailboxStore,
        operating_context: TeamOperatingContextService,
    ) -> None:
        self._repo = repo
        self._mailbox = mailbox
        self._operating_context = operating_context

    def build(
        self,
        *,
        team: AgentTeam,
        team_session: PersistentAgentSession | None,
        thread: AgentMessageThread | None,
        member_sessions: list[PersistentAgentSession],
    ) -> TeamRuntimeState:
        runtime_metadata = team_runtime_metadata(team)
        status = str(runtime_metadata.get("status") or TEAM_RUNTIME_STOPPED)
        workspace_runtime_id = _uuid_or_none(
            runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
        )
        runtime = (
            self._repo.runtime(team.workspace_id, workspace_runtime_id)
            if workspace_runtime_id is not None
            else None
        )
        generated_at = datetime.now(UTC)
        last_iteration_message = (
            self._mailbox._last_iteration_message(team, thread)
            if thread is not None
            else None
        )
        last_message_at = (
            self._mailbox._last_message_at(team, thread) if thread is not None else None
        )

        return TeamRuntimeState(
            workspace_id=team.workspace_id,
            team_id=team.id,
            generated_at=generated_at,
            status=status,
            team_session_id=team_session.id if team_session is not None else None,
            team_session_key=team_session.session_key if team_session is not None else None,
            thread_id=thread.id if thread is not None else None,
            workspace_runtime_id=runtime.id if runtime is not None else None,
            runtime_status=runtime.status if runtime is not None else None,
            runtime_space_id=(
                runtime.runtime_space_id if runtime is not None else team.runtime_space_id
            ),
            last_iteration=_last_iteration(runtime_metadata, last_iteration_message),
            last_message_at=last_message_at,
            runtime_health=_runtime_health(
                status=status,
                runtime=runtime,
                metadata=runtime_metadata,
                generated_at=generated_at,
            ),
            member_session_count=len(member_sessions),
            member_agent_ids=[
                session.agent_profile_id
                for session in member_sessions
                if session.agent_profile_id is not None
            ],
            operating_policy=self._operating_context.operating_policy(team=team),
            memory_summary=self._operating_context.memory_summary(team=team),
            metadata=runtime_metadata,
        )
