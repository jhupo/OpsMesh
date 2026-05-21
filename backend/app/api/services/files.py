from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.artifacts.models import Artifact
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage import LocalStorage

T = TypeVar("T")


class WorkspaceFileService:
    def __init__(self, session: Session, storage: LocalStorage, max_upload_bytes: int) -> None:
        self._session = session
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    def list_files(self, workspace_id: UUID, page: PageParams) -> tuple[list[WorkspaceFile], int]:
        statement = (
            select(WorkspaceFile)
            .where(WorkspaceFile.workspace_id == workspace_id, WorkspaceFile.status == "active")
            .order_by(WorkspaceFile.created_at.desc())
        )
        return self._page(statement, page)

    def upload_file(
        self,
        *,
        workspace_id: UUID,
        uploaded_by_user_id: UUID,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> WorkspaceFile:
        if len(content) > self._max_upload_bytes:
            raise ValueError("File exceeds maximum upload size")

        checksum = sha256(content).hexdigest()
        sanitized_filename = safe_filename(filename)
        file = WorkspaceFile(
            workspace_id=workspace_id,
            uploaded_by_user_id=uploaded_by_user_id,
            filename=sanitized_filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=f"workspaces/{workspace_id}/files/{checksum}/{sanitized_filename}",
        )
        self._session.add(file)
        self._session.flush()
        self._storage.write(file.storage_key, content)
        self._session.commit()
        self._session.refresh(file)
        return file

    def read_file(
        self,
        workspace_id: UUID,
        file_id: UUID,
        user_id: UUID | None = None,
    ) -> tuple[WorkspaceFile, bytes]:
        file = self._session.get(WorkspaceFile, file_id)
        if file is None or file.workspace_id != workspace_id or file.status != "active":
            raise FileNotFoundError("Workspace file not found")
        self._record_access(
            workspace_id=workspace_id,
            workspace_file_id=file.id,
            artifact_id=None,
            user_id=user_id,
            action="file.download",
        )
        self._session.commit()
        return file, self._storage.read(file.storage_key)

    def list_artifacts(self, workspace_id: UUID, page: PageParams) -> tuple[list[Artifact], int]:
        statement = (
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .order_by(Artifact.created_at.desc())
        )
        return self._page(statement, page)

    def list_artifact_history(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        work_package_id: str,
    ) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.task_id == task_id,
                    Artifact.work_package_id == work_package_id,
                )
                .order_by(Artifact.version.desc(), Artifact.created_at.desc(), Artifact.id.desc())
            ).all()
        )

    def read_artifact(
        self,
        workspace_id: UUID,
        artifact_id: UUID,
        user_id: UUID | None = None,
    ) -> tuple[Artifact, bytes]:
        artifact = self._session.get(Artifact, artifact_id)
        if artifact is None or artifact.workspace_id != workspace_id:
            raise FileNotFoundError("Artifact not found")
        self._record_access(
            workspace_id=workspace_id,
            workspace_file_id=None,
            artifact_id=artifact.id,
            user_id=user_id,
            action="artifact.download",
        )
        self._session.commit()
        return artifact, self._storage.read(artifact.storage_key)

    def _record_access(
        self,
        *,
        workspace_id: UUID,
        workspace_file_id: UUID | None,
        artifact_id: UUID | None,
        user_id: UUID | None,
        action: str,
    ) -> None:
        self._session.add(
            FileAccessEvent(
                workspace_id=workspace_id,
                workspace_file_id=workspace_file_id,
                artifact_id=artifact_id,
                user_id=user_id,
                action=action,
                created_at=datetime.now(UTC),
            )
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
