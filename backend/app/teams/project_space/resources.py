from __future__ import annotations

from backend.app.artifacts.models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.teams.project_space.utils import counts


def storage_summary(artifacts: list[Artifact], files: list[WorkspaceFile]) -> dict[str, object]:
    artifact_bytes = sum(max(artifact.size_bytes, 0) for artifact in artifacts)
    file_bytes = sum(max(file.size_bytes, 0) for file in files)
    return {
        "total_bytes": artifact_bytes + file_bytes,
        "artifact_bytes": artifact_bytes,
        "workspace_file_bytes": file_bytes,
        "artifact_count": len(artifacts),
        "workspace_file_count": len(files),
        "artifact_type_counts": counts(artifact.artifact_type for artifact in artifacts),
        "file_content_type_counts": counts(file.content_type for file in files),
        "file_relationship": "metadata_inferred",
    }


def memory_summary(entries: list[WorkspaceMemoryEntry]) -> dict[str, object]:
    return {
        "entry_count": len(entries),
        "source_type_counts": counts(entry.source_type or "unknown" for entry in entries),
        "visibility_scope_counts": counts(entry.visibility_scope for entry in entries),
        "importance_total": sum(entry.importance for entry in entries),
        "relationship": "source_or_metadata_inferred",
    }
