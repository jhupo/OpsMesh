from __future__ import annotations

from uuid import UUID

from backend.app.agent_runtime.sessions import PersistentAgentSessionRef
from backend.app.core.typing import dict_or_none, uuid_or_none
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_SESSION_SCOPE,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)


def team_runtime_ref(workspace_id: UUID, team_id: UUID) -> PersistentAgentSessionRef:
    return PersistentAgentSessionRef(
        session_key=_team_session_key(workspace_id, team_id),
        workspace_id=workspace_id,
        scope_type=TEAM_RUNTIME_SESSION_SCOPE,
        scope_id=str(team_id),
    )


def team_bound_runtime_id(team: AgentTeam) -> UUID | None:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime = policy.get(TEAM_RUNTIME_STATUS_KEY)
    if not isinstance(runtime, dict):
        return None
    return uuid_or_none(runtime.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY))


def team_runtime_metadata(team: AgentTeam) -> dict[str, object]:
    value = team.default_task_policy.get(TEAM_RUNTIME_STATUS_KEY)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Team runtime metadata must be an object")
    return dict(value)


def _team_session_key(workspace_id: UUID, team_id: UUID) -> str:
    return f"{workspace_id}:{TEAM_RUNTIME_SESSION_SCOPE}:{team_id}"


def _member_session_key(workspace_id: UUID, team_id: UUID, agent_profile_id: UUID) -> str:
    return f"{workspace_id}:team_agent:{team_id}:{agent_profile_id}"


def _uuid_or_none(value: object) -> UUID | None:
    return uuid_or_none(value)


def _dict_or_none(value: object) -> dict[str, object] | None:
    return dict_or_none(value)
