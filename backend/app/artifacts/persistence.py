from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.storage import ObjectStorage
from backend.app.files.storage_transactions import (
    CompensatingObjectStorageWrites,
    ObjectStorageCompensationError,
    ObjectStorageKeyConflictError,
)


class ArtifactPersistenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ArtifactPersistenceService:
    def __init__(self, session: Session, storage: ObjectStorage) -> None:
        self._session = session
        self._storage = storage

    def persist_new(self, artifact: Artifact, content: bytes) -> Artifact:
        writes = CompensatingObjectStorageWrites(self._storage)
        phase = "database"
        try:
            self._session.add(artifact)
            self._session.flush()
            phase = "storage"
            writes.write_new(artifact.storage_key, content)
            phase = "database"
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            try:
                writes.compensate()
            except ObjectStorageCompensationError as compensation_exc:
                raise ArtifactPersistenceError(
                    "artifact_storage_compensation_failed",
                    "Artifact persistence failed and storage cleanup was incomplete",
                ) from compensation_exc
            if isinstance(exc, ObjectStorageCompensationError):
                raise ArtifactPersistenceError(
                    "artifact_storage_compensation_failed",
                    "Artifact persistence failed and storage cleanup was incomplete",
                ) from exc
            if isinstance(exc, ObjectStorageKeyConflictError):
                raise ArtifactPersistenceError(
                    "artifact_storage_conflict",
                    "Artifact storage destination already exists",
                ) from exc
            if phase == "storage":
                raise ArtifactPersistenceError(
                    "artifact_storage_write_failed",
                    "Artifact content could not be written to object storage",
                ) from exc
            raise ArtifactPersistenceError(
                "artifact_database_write_failed",
                "Artifact metadata could not be committed",
            ) from exc
        writes.complete()
        return artifact
