from __future__ import annotations

from opsmesh.resources.files.models import WorkspaceFile

FILE_SENSITIVITY_LEVELS = frozenset({"public", "internal", "confidential", "restricted"})
FILE_RUNTIME_ACCESS_MODES = frozenset({"allowed", "denied"})


def validate_file_runtime_policy(
    *,
    sensitivity: str,
    runtime_access: str,
) -> tuple[str, str]:
    if sensitivity not in FILE_SENSITIVITY_LEVELS:
        raise ValueError("Workspace file sensitivity is invalid")
    if runtime_access not in FILE_RUNTIME_ACCESS_MODES:
        raise ValueError("Workspace file runtime access policy is invalid")
    if sensitivity == "restricted" and runtime_access != "denied":
        raise ValueError("Restricted workspace files cannot be exposed to runtimes")
    return sensitivity, runtime_access


def runtime_file_denial_code(file: WorkspaceFile) -> str | None:
    validate_file_runtime_policy(
        sensitivity=file.sensitivity,
        runtime_access=file.runtime_access,
    )
    if file.runtime_access == "denied" or file.sensitivity == "restricted":
        return "project_input_runtime_access_denied"
    return None
