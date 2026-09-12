from __future__ import annotations

from datetime import datetime
from uuid import UUID

from backend.app.agents.memory.models import WorkspaceMemoryEntry
from backend.app.platform.common.typing import counts_by_value
from backend.app.workspace.storage.artifact_models import Artifact
from backend.app.workspace.storage.models import WorkspaceFile
from backend.app.workspace.teams.project_employees import employee_outputs
from backend.app.workspace.teams.project_governance import archive_policy, landing_rules, summary
from backend.app.workspace.teams.project_policies import SPACE_TIER_TEMPLATES
from backend.app.workspace.teams.project_runtime import (
    capacity_summary,
    max_project_space,
    reservation_summary,
    runtime_space_payload,
)
from backend.app.workspace.teams.project_tasks import project_task_items
from backend.app.workspace.teams.project_types import ProjectSpaceRecords


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
