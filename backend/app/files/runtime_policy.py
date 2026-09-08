from __future__ import annotations

from pathlib import PurePosixPath

from backend.app.files.models import WorkspaceFile

FILE_SENSITIVITY_LEVELS = frozenset(
    {"public", "internal", "confidential", "restricted"}
)
FILE_RUNTIME_ACCESS_MODES = frozenset({"allowed", "denied"})
MAX_RUNTIME_STAGED_FILE_BYTES = 536_870_912

_SENSITIVE_FILENAMES = frozenset(
    {
        ".env",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "netrc",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".kdbx", ".p12", ".pem", ".pfx"})


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
    normalized_name = PurePosixPath(file.filename).name.casefold()
    if (
        normalized_name in _SENSITIVE_FILENAMES
        or normalized_name.startswith(".env.")
        or PurePosixPath(normalized_name).suffix in _SENSITIVE_SUFFIXES
    ):
        return "project_input_sensitive_file_denied"
    return None
