from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from backend.app.files.models import WorkspaceFile
from backend.app.files.runtime_policy import runtime_file_denial_code
from backend.app.files.security import validate_storage_key
from backend.app.files.storage import ObjectStorage, StorageObjectTooLargeError

DEFAULT_AGENT_FILE_READ_MAX_BYTES = 1024 * 1024
DEFAULT_AGENT_READABLE_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/toml",
        "application/x-yaml",
        "application/xml",
        "text/csv",
        "text/markdown",
        "text/plain",
        "text/tab-separated-values",
        "text/x-python",
        "text/xml",
        "text/yaml",
    }
)


class WorkspaceFileReadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkspaceFileContent:
    file: WorkspaceFile
    content: str
    encoding: str = "utf-8"
    checksum_verified: bool = True


class WorkspaceFileContentReader:
    def __init__(
        self,
        storage: ObjectStorage,
        *,
        max_bytes: int = DEFAULT_AGENT_FILE_READ_MAX_BYTES,
        allowed_content_types: frozenset[str] = DEFAULT_AGENT_READABLE_CONTENT_TYPES,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("Agent file read limit must be positive")
        self._storage = storage
        self._max_bytes = max_bytes
        self._allowed_content_types = frozenset(
            item.strip().lower() for item in allowed_content_types if item.strip()
        )

    def read(self, file: WorkspaceFile, *, workspace_id: UUID) -> WorkspaceFileContent:
        if file.workspace_id != workspace_id or file.status != "active":
            raise WorkspaceFileReadError(
                "workspace_file_not_found",
                "Workspace file not found",
            )
        try:
            denial_code = runtime_file_denial_code(file)
        except ValueError as exc:
            raise WorkspaceFileReadError(
                "workspace_file_runtime_policy_invalid",
                "Workspace file runtime policy is invalid",
            ) from exc
        if denial_code is not None:
            raise WorkspaceFileReadError(
                denial_code,
                "Workspace file is blocked by its runtime access policy",
            )
        content_type = _normalized_content_type(file.content_type)
        if content_type not in self._allowed_content_types:
            raise WorkspaceFileReadError(
                "workspace_file_content_type_unsupported",
                f"Workspace file content type is not readable by agents: {content_type}",
            )
        if file.size_bytes < 0 or file.size_bytes > self._max_bytes:
            raise WorkspaceFileReadError(
                "workspace_file_too_large",
                "Workspace file exceeds the agent read limit",
            )
        try:
            storage_key = validate_storage_key(
                file.storage_key,
                expected_prefix=f"workspaces/{workspace_id}/files",
            )
            content = self._storage.read_limited(storage_key, self._max_bytes)
        except StorageObjectTooLargeError as exc:
            raise WorkspaceFileReadError(
                "workspace_file_too_large",
                "Workspace file exceeds the agent read limit",
            ) from exc
        except FileNotFoundError as exc:
            raise WorkspaceFileReadError(
                "workspace_file_object_missing",
                "Workspace file content is unavailable",
            ) from exc
        except ValueError as exc:
            raise WorkspaceFileReadError(
                "workspace_file_storage_invalid",
                "Workspace file storage location is invalid",
            ) from exc
        if len(content) != file.size_bytes:
            raise WorkspaceFileReadError(
                "workspace_file_size_mismatch",
                "Workspace file size does not match its stored metadata",
            )
        if sha256(content).hexdigest() != file.checksum_sha256:
            raise WorkspaceFileReadError(
                "workspace_file_checksum_mismatch",
                "Workspace file checksum verification failed",
            )
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise WorkspaceFileReadError(
                "workspace_file_encoding_unsupported",
                "Workspace file is not valid UTF-8 text",
            ) from exc
        return WorkspaceFileContent(file=file, content=decoded)


def _normalized_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()
