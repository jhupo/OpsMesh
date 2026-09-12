from __future__ import annotations

from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.content import WorkspaceFileContent
from backend.app.domains.workspace.storage.models import WorkspaceFile


def memory_entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": str(entry.id),
        "memory_layer": entry.memory_layer,
        "scope_type": entry.scope_type,
        "scope_id": entry.scope_id,
        "memory_key": entry.memory_key,
        "entry_type": entry.entry_type,
        "title": entry.title,
        "status": entry.status,
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "revision": entry.revision,
        "content_fingerprint": entry.content_fingerprint,
        "tags": list(entry.tags or []),
    }


def workspace_file_payload(file: WorkspaceFile) -> dict[str, object]:
    return {
        "id": str(file.id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "status": file.status,
        "sensitivity": file.sensitivity,
        "runtime_access": file.runtime_access,
    }


def workspace_file_content_payload(value: WorkspaceFileContent) -> dict[str, object]:
    return workspace_file_payload(value.file) | {
        "content": value.content,
        "encoding": value.encoding,
        "checksum_verified": value.checksum_verified,
        "trust_level": "untrusted_workspace_input",
    }


def artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "artifact_type": artifact.artifact_type,
        "review_status": artifact.review_status,
    }
