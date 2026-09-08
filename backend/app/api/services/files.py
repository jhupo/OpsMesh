from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.artifacts.models import Artifact
from backend.app.audit.service import AuditService
from backend.app.db.pagination import page_scalars
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.files.runtime_policy import validate_file_runtime_policy
from backend.app.files.security import safe_filename
from backend.app.files.storage import ObjectStorage
from backend.app.files.storage_transactions import CompensatingObjectStorageWrites
from backend.app.memory.indexing import WorkspaceMemoryIndexingService
from backend.app.tasks.models import Task, TaskStep

T = TypeVar("T")


class WorkspaceFileService:
    def __init__(self, session: Session, storage: ObjectStorage, max_upload_bytes: int) -> None:
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
        file_id = uuid4()
        file = WorkspaceFile(
            id=file_id,
            workspace_id=workspace_id,
            uploaded_by_user_id=uploaded_by_user_id,
            filename=sanitized_filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=f"workspaces/{workspace_id}/files/{file_id}/{sanitized_filename}",
        )
        writes = CompensatingObjectStorageWrites(self._storage)
        try:
            self._session.add(file)
            self._session.flush()
            writes.write_new(file.storage_key, content)
            self._session.commit()
        except Exception:
            try:
                self._session.rollback()
            finally:
                writes.compensate()
            raise
        writes.complete()
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

    def update_runtime_policy(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        user_id: UUID,
        sensitivity: str,
        runtime_access: str,
    ) -> WorkspaceFile:
        file = self._session.scalar(
            select(WorkspaceFile)
            .where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.id == file_id,
                WorkspaceFile.status == "active",
            )
            .with_for_update()
        )
        if file is None:
            raise FileNotFoundError("Workspace file not found")
        file.sensitivity, file.runtime_access = validate_file_runtime_policy(
            sensitivity=sensitivity,
            runtime_access=runtime_access,
        )
        WorkspaceMemoryIndexingService(self._session).refresh_file(
            workspace_id=workspace_id,
            file_id=file.id,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="file.runtime_policy.updated",
            target_type="workspace_file",
            target_id=file.id,
            metadata={
                "sensitivity": sensitivity,
                "runtime_access": runtime_access,
            },
        )
        self._session.commit()
        self._session.refresh(file)
        return file

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
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return []
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

    def list_final_output_artifact_history(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> tuple[Task | None, list[str], list[Artifact]]:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None, [], []

        work_package_ids = self._final_output_work_package_ids(workspace_id, task_id)
        if not work_package_ids:
            return task, [], []

        artifacts = list(
            self._session.scalars(
                select(Artifact)
                .where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.task_id == task_id,
                    Artifact.work_package_id.in_(work_package_ids),
                )
                .order_by(Artifact.created_at.desc(), Artifact.version.desc(), Artifact.id.desc())
            ).all()
        )
        return task, work_package_ids, artifacts

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
        return page_scalars(self._session, statement, page)

    def _final_output_work_package_ids(self, workspace_id: UUID, task_id: UUID) -> list[str]:
        steps = self._session.scalars(
            select(TaskStep)
            .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
            .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
        ).all()
        package_ids: list[str] = []
        seen: set[str] = set()
        for step in steps:
            package_id = step.work_package_id
            if package_id is None or package_id in seen:
                continue
            if not _is_final_output_step(step):
                continue
            seen.add(package_id)
            package_ids.append(package_id)
        return package_ids


def _is_final_output_step(step: TaskStep) -> bool:
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    expected_artifacts = (
        step.expected_artifacts if isinstance(step.expected_artifacts, list) else []
    )
    work_package_id = step.work_package_id or ""
    return (
        review_policy.get("mode") == "final_acceptance"
        or work_package_id == "manager-summary"
        or work_package_id.startswith("manager-summary-revision-")
        or "final_delivery" in expected_artifacts
    )
