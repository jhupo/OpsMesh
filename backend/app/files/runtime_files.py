from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.security import validate_runtime_relative_path
from backend.app.files.storage import ObjectStorage
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
    ) -> Path:
        file = ProductToolService(self._session).read_workspace_file(context, file_id)
        target = self._safe_runtime_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._storage.read(file.storage_key))
        return target

    def collect_artifact(
        self,
        *,
        context: ToolContext,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Artifact:
        artifact = ProductToolService(self._session).write_artifact(
            context,
            filename=filename,
            content=content,
            content_type=content_type,
        )
        self._session.commit()
        return artifact

    def _safe_runtime_path(self, relative_path: str) -> Path:
        path = (self._runtime_root / validate_runtime_relative_path(relative_path)).resolve()
        if not path.is_relative_to(self._runtime_root):
            raise ValueError("Runtime staging path escapes runtime root")
        return path
