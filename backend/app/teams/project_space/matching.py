from __future__ import annotations

from uuid import UUID

from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.teams.project_space.types import ProjectSpaceRelationshipIds


def workspace_file_matches_project(
    file: WorkspaceFile,
    relationship_ids: ProjectSpaceRelationshipIds,
) -> bool:
    return metadata_matches_project(file.file_metadata, relationship_ids)


def memory_entry_matches_project(
    entry: WorkspaceMemoryEntry,
    relationship_ids: ProjectSpaceRelationshipIds,
) -> bool:
    if source_matches_project(entry.source_type, entry.source_id, relationship_ids):
        return True
    if entry.created_by_agent_run_id in relationship_ids.agent_run_ids:
        return True
    return metadata_matches_project(entry.memory_metadata, relationship_ids)


def metadata_matches_project(
    metadata: object,
    relationship_ids: ProjectSpaceRelationshipIds,
) -> bool:
    payload = metadata if isinstance(metadata, dict) else {}
    if _has_uuid_value(
        payload,
        {"team_id", "agent_team_id", "agentTeamId"},
        {relationship_ids.team_id},
    ):
        return True
    if _has_uuid_value(
        payload,
        {"runtime_space_id", "runtimeSpaceId", "project_space_id", "projectSpaceId"},
        relationship_ids.runtime_space_ids,
    ):
        return True
    if _has_uuid_value(payload, {"task_id", "taskId"}, relationship_ids.task_ids):
        return True
    if _has_uuid_value(payload, {"task_step_id", "taskStepId"}, relationship_ids.task_step_ids):
        return True
    if _has_uuid_value(
        payload,
        {"agent_run_id", "agentRunId", "run_id", "runId"},
        relationship_ids.agent_run_ids,
    ):
        return True
    return _has_uuid_value(payload, {"artifact_id", "artifactId"}, relationship_ids.artifact_ids)


def source_matches_project(
    source_type: str | None,
    source_id: str | None,
    relationship_ids: ProjectSpaceRelationshipIds,
) -> bool:
    if source_type is None or source_id is None:
        return False
    if source_type in {"team", "agent_team"}:
        return source_id == str(relationship_ids.team_id)
    if source_type == "task":
        return source_id in {str(task_id) for task_id in relationship_ids.task_ids}
    if source_type == "task_step":
        return source_id in {str(step_id) for step_id in relationship_ids.task_step_ids}
    if source_type == "agent_run":
        return source_id in {str(run_id) for run_id in relationship_ids.agent_run_ids}
    if source_type == "artifact":
        return source_id in {str(artifact_id) for artifact_id in relationship_ids.artifact_ids}
    if source_type in {"runtime", "runtime_space"}:
        return source_id in {str(space_id) for space_id in relationship_ids.runtime_space_ids}
    return False


def _has_uuid_value(payload: dict[str, object], keys: set[str], expected: set[UUID]) -> bool:
    expected_text = {str(value) for value in expected}
    if not expected_text:
        return False
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value in expected_text:
            return True
        if isinstance(value, UUID) and str(value) in expected_text:
            return True
        if isinstance(value, list) and any(str(item) in expected_text for item in value):
            return True
    return False
