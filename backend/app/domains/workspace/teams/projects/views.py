from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import UUID

from backend.app.core.utils import counts_by_value
from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUSES
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.teams.execution.overview_contracts import DONE_TASK_STATUSES
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.domains.workspace.teams.projects.employees import employee_outputs
from backend.app.domains.workspace.teams.projects.policies import SPACE_TIER_TEMPLATES
from backend.app.domains.workspace.teams.projects.runtime import (
    capacity_summary,
    max_project_space,
    reservation_summary,
    runtime_space_payload,
)
from backend.app.domains.workspace.teams.projects.types import ProjectSpaceRecords
from backend.app.runtime.environment.spaces.models import RuntimeSpace


def build_project_space_response(
    *,
    workspace_id: UUID,
    team_id: UUID,
    generated_at: datetime,
    include_completed: bool,
    limit: int,
    records: ProjectSpaceRecords,
) -> dict[str, object]:
    runtime_space_items = [
        runtime_space_payload(
            space,
            quotas=records.quotas_by_space.get(space.id, []),
            reservations=records.reservations_by_space.get(space.id, []),
            events=records.events_by_space.get(space.id, []),
        )
        for space in records.runtime_spaces
    ]
    storage = storage_summary(records.artifacts, records.files)
    memory = memory_summary(records.memories)
    reservations = reservation_summary(
        [reservation for items in records.reservations_by_space.values() for reservation in items]
    )
    return {
        "workspace_id": workspace_id,
        "team_id": team_id,
        "generated_at": generated_at,
        "team": _team_payload(records),
        "summary": summary(records=records, include_completed=include_completed, limit=limit),
        "runtime_spaces": runtime_space_items,
        "capacity": capacity_summary(
            runtime_space_items=runtime_space_items,
            storage=storage,
            memory=memory,
            reservations=reservations,
        ),
        "storage": storage,
        "memory": memory,
        "employee_outputs": employee_outputs(records),
        "project_tasks": project_task_items(records),
        "landing_rules": landing_rules(records.team, records.runtime_spaces),
        "space_tiers": SPACE_TIER_TEMPLATES,
        "max_project_space": max_project_space(runtime_space_items),
        "archive_policy": archive_policy(records.team, records.runtime_spaces, records),
    }


def _team_payload(records: ProjectSpaceRecords) -> dict[str, object]:
    team = records.team
    return {
        "id": team.id,
        "name": team.name,
        "team_type": team.team_type,
        "status": team.status,
        "manager_agent_profile_id": team.manager_agent_profile_id,
        "primary_runtime_space_id": team.runtime_space_id,
        "coordination_rules": team.coordination_rules,
        "default_task_policy": team.default_task_policy,
    }


def storage_summary(artifacts: list[Artifact], files: list[WorkspaceFile]) -> dict[str, object]:
    artifact_bytes = sum(max(artifact.size_bytes, 0) for artifact in artifacts)
    file_bytes = sum(max(file.size_bytes, 0) for file in files)
    return {
        "total_bytes": artifact_bytes + file_bytes,
        "artifact_bytes": artifact_bytes,
        "workspace_file_bytes": file_bytes,
        "artifact_count": len(artifacts),
        "workspace_file_count": len(files),
        "artifact_type_counts": counts_by_value(artifact.artifact_type for artifact in artifacts),
        "file_content_type_counts": counts_by_value(file.content_type for file in files),
        "file_relationship": "metadata_inferred",
    }


def memory_summary(entries: list[WorkspaceMemoryEntry]) -> dict[str, object]:
    return {
        "entry_count": len(entries),
        "source_type_counts": counts_by_value(entry.source_type or "unknown" for entry in entries),
        "visibility_scope_counts": counts_by_value(entry.visibility_scope for entry in entries),
        "importance_total": sum(entry.importance for entry in entries),
        "relationship": "source_or_metadata_inferred",
    }


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
        "task_status_counts": counts_by_value(task.status for task in records.tasks),
        "step_count": len(records.steps),
        "step_status_counts": counts_by_value(step.status for step in records.steps),
        "run_count": len(records.runs),
        "active_run_count": sum(1 for run in records.runs if run.status in ACTIVE_RUN_STATUSES),
        "run_status_counts": counts_by_value(run.status for run in records.runs),
        "artifact_count": len(records.artifacts),
        "workspace_file_count": len(records.files),
        "memory_entry_count": len(records.memories),
        "runtime_space_count": len(records.runtime_spaces),
    }


def project_task_items(records: ProjectSpaceRecords) -> list[dict[str, object]]:
    steps_by_task: dict[UUID, list[TaskStep]] = defaultdict(list)
    runs_by_task: dict[UUID, list[AgentRun]] = defaultdict(list)
    artifacts_by_task: dict[UUID, list[Artifact]] = defaultdict(list)
    messages_by_task: dict[UUID, list[TaskMessage]] = defaultdict(list)
    for step in records.steps:
        steps_by_task[step.task_id].append(step)
    for run in records.runs:
        if run.task_id is not None:
            runs_by_task[run.task_id].append(run)
    for artifact in records.artifacts:
        if artifact.task_id is not None:
            artifacts_by_task[artifact.task_id].append(artifact)
    for message in records.messages:
        messages_by_task[message.task_id].append(message)
    return [
        task_item(
            task,
            steps=steps_by_task.get(task.id, []),
            runs=runs_by_task.get(task.id, []),
            artifacts=artifacts_by_task.get(task.id, []),
            messages=messages_by_task.get(task.id, []),
        )
        for task in records.tasks
    ]

def task_item(
    task: Task,
    *,
    steps: list[TaskStep],
    runs: list[AgentRun],
    artifacts: list[Artifact],
    messages: list[TaskMessage],
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "runtime_space_id": task.runtime_space_id,
        "step_count": len(steps),
        "step_status_counts": counts_by_value(step.status for step in steps),
        "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
        "run_status_counts": counts_by_value(run.status for run in runs),
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(max(artifact.size_bytes, 0) for artifact in artifacts),
        "message_count": len(messages),
        "updated_at": task.updated_at,
    }
