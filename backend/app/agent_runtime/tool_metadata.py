from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import AgentRuntimeContext
from backend.app.runs.models import AgentRun


def tool_metadata(
    *,
    context: AgentRuntimeContext,
    tool_name: str,
    tool_kind: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "provenance": "backend_tool_executor",
        "tool_name": tool_name,
        "tool_kind": tool_kind,
        "workspace_id": str(context.workspace_id),
        "run_id": str(context.run_id),
        "task_id": str(context.task_id) if context.task_id is not None else None,
        "user_id": str(context.user_id) if context.user_id is not None else None,
    }
    context_metadata = context.metadata if isinstance(context.metadata, dict) else {}
    for key in ("trace_id", "span_id", "persistent_session_key"):
        value = context_metadata.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    team_context = context_metadata.get("team_context")
    if isinstance(team_context, dict):
        team_metadata = _team_metadata(team_context)
        if team_metadata:
            metadata["team"] = team_metadata
    if extra:
        metadata.update(extra)
    return metadata


def product_review_context(context: AgentRuntimeContext) -> dict[str, object]:
    return {
        "agent_run_id": str(context.run_id),
        "task_id": str(context.task_id) if context.task_id is not None else None,
        "allowed_tools": list(context.allowed_tools),
        "metadata": dict(context.metadata),
    }


def agent_profile_id_for_context(
    session: Session,
    context: AgentRuntimeContext,
) -> UUID | None:
    run = session.get(AgentRun, context.run_id)
    if run is None or run.workspace_id != context.workspace_id:
        return None
    return run.agent_profile_id


def _team_metadata(team_context: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("team_id", "team_name", "team_type"):
        value = team_context.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    current_member = team_context.get("current_member")
    if isinstance(current_member, dict):
        member_metadata = {
            key: value
            for key, value in current_member.items()
            if key
            in {
                "member_id",
                "agent_profile_id",
                "reports_to_member_id",
                "team_role",
                "department",
                "position_title",
            }
            and isinstance(value, str)
        }
        if member_metadata:
            metadata["current_member"] = member_metadata
    runtime = team_context.get("runtime")
    if isinstance(runtime, dict):
        runtime_metadata = _runtime_metadata(runtime)
        if runtime_metadata:
            metadata["runtime"] = runtime_metadata
    return metadata


def _runtime_metadata(runtime: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in (
        "status",
        "workspace_runtime_id",
        "runtime_status",
        "runtime_space_id",
        "thread_id",
        "team_session_id",
    ):
        value = runtime.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    member_session_count = runtime.get("member_session_count")
    if isinstance(member_session_count, int) and not isinstance(member_session_count, bool):
        metadata["member_session_count"] = member_session_count
    return metadata
