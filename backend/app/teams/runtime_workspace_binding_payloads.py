from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.teams.runtime_refs import team_runtime_metadata


def bind_runtime_metadata(
    *,
    team: AgentTeam,
    runtime: WorkspaceRuntime,
    actor_user_id: UUID,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    policy = dict(team.default_task_policy or {})
    runtime_metadata = team_runtime_metadata(team)
    runtime_metadata[TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY] = str(runtime.id)
    runtime_metadata["runtime_bound_at"] = datetime.now(UTC).isoformat()
    runtime_metadata["updated_by_user_id"] = str(actor_user_id)
    _optional_metadata(runtime_metadata, reason=reason, metadata=metadata)
    policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
    team.default_task_policy = policy
    return runtime_metadata


def ensure_runtime_metadata(
    *,
    team: AgentTeam,
    runtime: WorkspaceRuntime,
    resolved_template_id: UUID | None,
    created: bool,
    started: bool,
    actor_user_id: UUID,
    start: bool,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    policy = dict(team.default_task_policy or {})
    runtime_metadata = team_runtime_metadata(team)
    ensured_at = datetime.now(UTC).isoformat()
    runtime_metadata.update(
        {
            "status": TEAM_RUNTIME_RUNNING
            if start
            else runtime_metadata.get("status", TEAM_RUNTIME_STOPPED),
            TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY: str(runtime.id),
            "runtime_bound_at": runtime_metadata.get("runtime_bound_at") or ensured_at,
            "runtime_ensured_at": ensured_at,
            "runtime_created": created,
            "runtime_started": started,
            "updated_by_user_id": str(actor_user_id),
        }
    )
    if resolved_template_id is not None:
        runtime_metadata["runtime_template_id"] = str(resolved_template_id)
    _optional_metadata(runtime_metadata, reason=reason, metadata=metadata)
    policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
    team.default_task_policy = policy
    return runtime_metadata


def attach_team_runtime_capabilities(
    *,
    team: AgentTeam,
    runtime: WorkspaceRuntime,
    bound_at: str,
    ensured_at: str | None = None,
) -> None:
    capability = {
        "workspace_id": str(team.workspace_id),
        "team_id": str(team.id),
        "team_name": team.name,
        "bound_at": bound_at,
    }
    if ensured_at is not None:
        capability["ensured_at"] = ensured_at
    capabilities = dict(runtime.capabilities or {})
    capabilities["team_runtime"] = capability
    runtime.capabilities = capabilities


def _optional_metadata(
    runtime_metadata: dict[str, object],
    *,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> None:
    if reason:
        runtime_metadata["reason"] = reason
    if metadata:
        runtime_metadata["metadata"] = dict(metadata)
