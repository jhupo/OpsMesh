from __future__ import annotations

from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.teams.execution_overview_constants import DONE_TASK_STATUSES
from backend.app.teams.models import AgentTeam
from backend.app.teams.project_space.policies import ACTIVE_RUN_STATUSES
from backend.app.teams.project_space.types import ProjectSpaceRecords
from backend.app.teams.project_space.utils import counts


def landing_rules(team: AgentTeam, spaces: list[RuntimeSpace]) -> dict[str, object]:
    primary_space = next((space for space in spaces if space.id == team.runtime_space_id), None)
    return {
        "team_runtime_space_id": team.runtime_space_id,
        "task_runtime_space_source": "task.runtime_space_id",
        "step_runtime_space_source": "task_step.runtime_space_id",
        "run_runtime_space_source": "agent_run.runtime_space_id",
        "artifact_source": "artifact.task_id_or_agent_run_id",
        "workspace_file_source": "workspace_file.metadata",
        "memory_source": "workspace_memory.source_or_metadata",
        "default_storage_policy": primary_space.storage_policy if primary_space is not None else {},
        "default_cleanup_policy": primary_space.cleanup_policy if primary_space is not None else {},
    }


def archive_policy(
    team: AgentTeam,
    spaces: list[RuntimeSpace],
    records: ProjectSpaceRecords,
) -> dict[str, object]:
    completed_count = sum(1 for task in records.tasks if task.status in DONE_TASK_STATUSES)
    policies = [
        {
            "runtime_space_id": space.id,
            "scope": space.scope,
            "cleanup_policy": space.cleanup_policy,
            "storage_policy": space.storage_policy,
        }
        for space in spaces
    ]
    recommended_actions: list[str] = []
    if completed_count:
        recommended_actions.append("archive_completed_project_tasks")
    if records.files:
        recommended_actions.append("classify_workspace_files_by_project_metadata")
    if not team.runtime_space_id:
        recommended_actions.append("bind_team_runtime_space")
    return {
        "completed_task_count": completed_count,
        "space_policies": policies,
        "recommended_actions": recommended_actions,
    }


def summary(
    *,
    records: ProjectSpaceRecords,
    include_completed: bool,
    limit: int,
) -> dict[str, object]:
    return {
        "include_completed": include_completed,
        "limit": limit,
        "task_count": len(records.tasks),
        "task_status_counts": counts(task.status for task in records.tasks),
        "step_count": len(records.steps),
        "step_status_counts": counts(step.status for step in records.steps),
        "run_count": len(records.runs),
        "active_run_count": sum(1 for run in records.runs if run.status in ACTIVE_RUN_STATUSES),
        "run_status_counts": counts(run.status for run in records.runs),
        "artifact_count": len(records.artifacts),
        "workspace_file_count": len(records.files),
        "memory_entry_count": len(records.memories),
        "runtime_space_count": len(records.runtime_spaces),
    }
