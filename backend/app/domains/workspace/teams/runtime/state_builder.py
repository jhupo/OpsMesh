from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.app.core.common.values import coerce_int_or_zero, datetime_or_none
from backend.app.domains.agents.messages.models import AgentMessage, AgentMessageThread
from backend.app.domains.agents.sessions.models import PersistentAgentSession
from backend.app.domains.workspace.teams.models import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_STALL_THRESHOLD,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
    AgentTeam,
)
from backend.app.domains.workspace.teams.organization.service import (
    TeamOperatingContextService,
)
from backend.app.domains.workspace.teams.runtime.mailbox import TeamRuntimeMailboxStore
from backend.app.domains.workspace.teams.runtime.refs import (
    _dict_or_none,
    _uuid_or_none,
    team_runtime_metadata,
)
from backend.app.domains.workspace.teams.runtime.repository import TeamRuntimeRepository
from backend.app.runtime.environment.models import WorkspaceRuntime


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
            self._mailbox._last_iteration_message(team, thread) if thread is not None else None
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


def _last_iteration(
    runtime_metadata: dict[str, object],
    message: AgentMessage | None,
) -> dict[str, object] | None:
    metadata_iteration = _dict_or_none(runtime_metadata.get("last_iteration"))
    if metadata_iteration is not None:
        return metadata_iteration
    if message is None:
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    message_iteration = _dict_or_none(payload.get("iteration"))
    if message_iteration is not None:
        return message_iteration
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
    generated_at: datetime,
) -> str:
    if status == TEAM_RUNTIME_STOPPED:
        return "stopped"
    if status == TEAM_RUNTIME_PAUSED:
        return "paused"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY) is not None and runtime is None:
        return "degraded"

    last_heartbeat_at = datetime_or_none(metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    if metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    if _runtime_stalled(metadata):
        return "degraded"
    if _last_worker_failure_active(metadata):
        return "degraded"
    return "healthy"


def _runtime_stalled(metadata: dict[str, object]) -> bool:
    if metadata.get("stalled_at"):
        return True
    return coerce_int_or_zero(metadata.get("stall_count")) >= TEAM_RUNTIME_STALL_THRESHOLD


def _last_worker_failure_active(metadata: dict[str, object]) -> bool:
    failure = metadata.get("last_worker_failure")
    if not isinstance(failure, dict):
        return False
    return failure.get("status") in {"retrying", "failed"}
