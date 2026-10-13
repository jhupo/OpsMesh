from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.agents.sessions.models import AgentSessionMetadata
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.teams.management.models import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
    AgentTeam,
)
from opsmesh.teams.organization.service import TeamOperatingContextService
from opsmesh.teams.sessions.mailbox import TeamMailboxStore
from opsmesh.teams.sessions.refs import _dict_or_none, _uuid_or_none, team_runtime_metadata
from opsmesh.teams.sessions.repository import TeamOperationRepository


@dataclass(frozen=True)
class TeamOperationSnapshot:
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
    last_advancement: dict[str, object] | None
    last_message_at: datetime | None
    runtime_health: str
    member_session_count: int
    member_agent_ids: list[UUID]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    metadata: dict[str, object]


class TeamOperationSnapshotBuilder:
    """Assemble the runtime state snapshot returned to APIs and schedulers."""

    def __init__(
        self,
        *,
        repo: TeamOperationRepository,
        mailbox: TeamMailboxStore,
        operating_context: TeamOperatingContextService,
    ) -> None:
        self._repo = repo
        self._mailbox = mailbox
        self._operating_context = operating_context

    def build(
        self,
        *,
        team: AgentTeam,
        team_session: AgentSessionMetadata | None,
        thread: AgentMessageThread | None,
        member_sessions: list[AgentSessionMetadata],
    ) -> TeamOperationSnapshot:
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
        last_advancement_message = (
            self._mailbox._last_advancement_message(team, thread) if thread is not None else None
        )
        last_message_at = (
            self._mailbox._last_message_at(team, thread) if thread is not None else None
        )

        return TeamOperationSnapshot(
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
            last_advancement=_last_advancement(runtime_metadata, last_advancement_message),
            last_message_at=last_message_at,
            runtime_health=_runtime_health(
                status=status,
                runtime=runtime,
                metadata=runtime_metadata,
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


def _last_advancement(
    runtime_metadata: dict[str, object],
    message: AgentMessage | None,
) -> dict[str, object] | None:
    metadata_advancement = _dict_or_none(runtime_metadata.get("last_advancement"))
    if metadata_advancement is not None:
        return metadata_advancement
    if message is None:
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    message_advancement = _dict_or_none(payload.get("advancement"))
    if message_advancement is not None:
        return message_advancement
    return {
        "status": str(payload.get("status") or "unknown"),
        "summary": _dict_or_none(payload.get("summary")) or {},
        "recorded_at": message.created_at.isoformat(),
    }


def _runtime_health(
    *,
    status: str,
    runtime: WorkspaceRuntime | None,
    metadata: dict[str, object],
) -> str:
    if status == TEAM_RUNTIME_STOPPED:
        return "stopped"
    if status == TEAM_RUNTIME_PAUSED:
        return "paused"
    if runtime is not None and runtime.status != "active":
        return "degraded"
    if metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY) is not None and runtime is None:
        return "degraded"

    return "healthy"
