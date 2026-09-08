from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.models import FileAccessEvent
from backend.app.files.runtime_policy import (
    MAX_RUNTIME_STAGED_FILE_BYTES,
    runtime_file_denial_code,
)
from backend.app.files.security import validate_runtime_relative_path, validate_storage_key
from backend.app.files.storage import ObjectStorage, StorageObjectTooLargeError
from backend.app.tools.context import ToolContext
from backend.app.tools.product_tools.service import ProductToolService


class RuntimeFileService:
    def __init__(self, session: Session, storage: ObjectStorage, runtime_root: str) -> None:
        self._session = session
        self._storage = storage
        self._runtime_root = Path(runtime_root).resolve()

    def stage_workspace_file(
        self,
        *,
        context: ToolContext,
        file_id: UUID,
        relative_path: str,
        allowed_file_ids: set[UUID],
    ) -> Path:
        file = ProductToolService(self._session).resolve_workspace_file(
            context,
            file_id,
            allowed_file_ids=allowed_file_ids,
        )
        target = self._safe_runtime_path(relative_path)
        denial_code = runtime_file_denial_code(file)
        if denial_code is not None:
            raise ValueError("Workspace file is blocked by its runtime access policy")
        if file.size_bytes < 0 or file.size_bytes > MAX_RUNTIME_STAGED_FILE_BYTES:
            raise ValueError("Workspace file exceeds the runtime staging limit")
        storage_key = validate_storage_key(
            file.storage_key,
            expected_prefix=f"workspaces/{context.workspace_id}/files",
        )
        try:
            content = self._storage.read_limited(storage_key, file.size_bytes)
        except StorageObjectTooLargeError as exc:
            raise ValueError("Workspace file size does not match its metadata") from exc
        if len(content) != file.size_bytes or sha256(content).hexdigest() != file.checksum_sha256:
            raise ValueError("Workspace file failed runtime staging integrity validation")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self._session.add(
            FileAccessEvent(
                workspace_id=context.workspace_id,
                workspace_file_id=file.id,
                artifact_id=None,
                user_id=None,
                action="stage_to_runtime",
                created_at=datetime.now(UTC),
            )
        )
        self._session.flush()
        return target

    def collect_artifact(
        self,
        *,
        context: ToolContext,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Artifact:
        return ProductToolService(self._session, storage=self._storage).write_artifact(
            context,
            filename=filename,
            content=content,
            content_type=content_type,
        )

    def _safe_runtime_path(self, relative_path: str) -> Path:
        path = (self._runtime_root / validate_runtime_relative_path(relative_path)).resolve()
        if not path.is_relative_to(self._runtime_root):
            raise ValueError("Runtime staging path escapes runtime root")
        return path
