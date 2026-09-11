
from backend.app.agent_runtime.contracts import AgentRunTracing
from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task

from .utils import json_safe


def agent_run_tracing(
    *,
    run: AgentRun,
    task: Task | None,
    profile: AgentProfile,
    allowed_tools: tuple[str, ...],
    metadata: dict[str, object],
) -> AgentRunTracing:
    trace_metadata = trace_metadata_for_agent_run(
        run=run,
        task=task,
        profile=profile,
        allowed_tools=allowed_tools,
        metadata=metadata,
    )
    return AgentRunTracing(
        workflow_name=agent_run_workflow_name(task),
        trace_id=optional_trace_string(metadata.get("trace_id")),
        group_id=agent_run_trace_group_id(run, task, metadata),
        metadata=trace_metadata,
    )


def trace_metadata_for_agent_run(
    *,
    run: AgentRun,
    task: Task | None,
    profile: AgentProfile,
    allowed_tools: tuple[str, ...],
    metadata: dict[str, object],
) -> dict[str, object]:
    trace_metadata: dict[str, object] = {
        "workspace_id": str(run.workspace_id),
        "run_id": str(run.id),
        "task_id": str(run.task_id) if run.task_id is not None else None,
        "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
        "agent_profile_id": str(profile.id) if profile.id is not None else None,
        "agent_role": profile.role,
        "run_model": metadata.get("run_model"),
        "model_provider_provider": metadata.get("model_provider_provider"),
        "model_provider_credential_id": metadata.get("model_provider_credential_id"),
        "model_provider_model_api": metadata.get("model_provider_model_api"),
        "authorization_snapshot_version": metadata.get("authorization_snapshot_version"),
        "allowed_tools": list(allowed_tools),
    }
    for key in ("trace_id", "span_id", "parent_span_id", "persistent_session_key"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            trace_metadata[key] = value
    team_context = metadata.get("team_context")
    if isinstance(team_context, dict):
        trace_metadata["team"] = team_trace_metadata(team_context)
    tool_continuations = metadata.get("tool_continuations")
    if isinstance(tool_continuations, list):
        trace_metadata["tool_continuations"] = [
            item
            for item in (json_safe(item) for item in tool_continuations)
            if isinstance(item, dict)
        ]
    if task is not None:
        trace_metadata["agent_team_id"] = str(task.agent_team_id) if task.agent_team_id else None
    return {key: value for key, value in trace_metadata.items() if value is not None}


def agent_run_workflow_name(task: Task | None) -> str:
    if task is not None and task.agent_team_id is not None:
        return "opsmesh.team_agent_run"
    return "opsmesh.agent_run"


def agent_run_trace_group_id(
    run: AgentRun,
    task: Task | None,
    metadata: dict[str, object],
) -> str:
    persistent_session_key = metadata.get("persistent_session_key")
    if isinstance(persistent_session_key, str) and persistent_session_key:
        return persistent_session_key
    if task is not None and task.agent_team_id is not None:
        return f"team:{task.agent_team_id}"
    if run.task_id is not None:
        return f"task:{run.task_id}"
    return f"workspace:{run.workspace_id}"


def team_trace_metadata(team_context: dict[str, object]) -> dict[str, object]:
    trace_team: dict[str, object] = {}
    for key in ("team_id", "team_name", "team_type", "team_status"):
        value = team_context.get(key)
        if isinstance(value, str) and value:
            trace_team[key] = value
    current_member = team_context.get("current_member")
    if isinstance(current_member, dict):
        trace_team["current_member"] = {
            key: value
            for key, value in current_member.items()
            if key in {"agent_profile_id", "team_role", "status"} and isinstance(value, str)
        }
    runtime = team_context.get("runtime")
    if isinstance(runtime, dict):
        runtime_metadata: dict[str, object] = {}
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
                runtime_metadata[key] = value
        member_session_count = runtime.get("member_session_count")
        if isinstance(member_session_count, int) and not isinstance(member_session_count, bool):
            runtime_metadata["member_session_count"] = member_session_count
        if runtime_metadata:
            trace_team["runtime"] = runtime_metadata
    return trace_team


def optional_trace_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
