from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.security import safe_filename
from backend.app.files.storage import ObjectStorage
from backend.app.files.storage_transactions import (
    CompensatingObjectStorageWrites,
    ObjectStorageCompensationError,
    ObjectStorageKeyConflictError,
)
from backend.app.projects.models import AgentRunProjectSnapshot, WorkspaceProject
from backend.app.projects.run_manifest import RunProjectOutput
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import TaskStep


@dataclass(slots=True)
class PreparedProjectArtifacts:
    session: Session
    writes: CompensatingObjectStorageWrites
    artifacts: list[Artifact]
    new_artifacts: list[Artifact]

    def commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            self._compensate()
            raise
        self.writes.complete()

    def abort(self) -> None:
        self.session.rollback()
        self._compensate()

    def _compensate(self) -> None:
        try:
            self.writes.compensate()
        except ObjectStorageCompensationError as exc:
            raise ProjectRunIOError(
                code="project_output_compensation_failed",
                message="Project output persistence failed and storage cleanup was incomplete",
                stage="output_harvest",
                retryable=False,
            ) from exc


class ProjectOutputArtifactWriter:
    def __init__(self, session: Session, storage: ObjectStorage) -> None:
        self._session = session
        self._storage = storage

    def prepare(
        self,
        *,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        outputs: list[tuple[RunProjectOutput, bytes]],
    ) -> PreparedProjectArtifacts:
        self._lock_project(run, snapshot)
        artifacts: list[Artifact] = []
        new_artifacts: list[tuple[Artifact, bytes]] = []
        for output, content in outputs:
            existing = self._existing(run, output)
            if existing is not None:
                self._validate_existing(existing, output, content, snapshot)
                artifacts.append(existing)
                continue
            artifact = self._new_artifact(run, snapshot, output, content)
            self._session.add(artifact)
            artifacts.append(artifact)
            new_artifacts.append((artifact, content))

        writes = CompensatingObjectStorageWrites(self._storage)
        try:
            self._session.flush()
            for artifact, content in new_artifacts:
                writes.write_new(artifact.storage_key, content)
        except Exception as exc:
            self._session.rollback()
            try:
                writes.compensate()
            except ObjectStorageCompensationError as compensation_exc:
                raise ProjectRunIOError(
                    code="project_output_compensation_failed",
                    message=(
                        "Project output persistence failed and storage cleanup was incomplete"
                    ),
                    stage="output_harvest",
                    retryable=False,
                ) from compensation_exc
            if isinstance(exc, ObjectStorageKeyConflictError):
                raise ProjectRunIOError(
                    code="project_output_storage_conflict",
                    message="A project output storage destination already exists",
                    stage="output_harvest",
                    retryable=False,
                ) from exc
            raise
        return PreparedProjectArtifacts(
            session=self._session,
            writes=writes,
            artifacts=artifacts,
            new_artifacts=[item[0] for item in new_artifacts],
        )

    def _lock_project(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
    ) -> None:
        project_id = self._session.scalar(
            select(WorkspaceProject.id)
            .where(
                WorkspaceProject.workspace_id == run.workspace_id,
                WorkspaceProject.id == snapshot.project_id,
            )
            .with_for_update()
        )
        if project_id is None:
            raise ProjectRunIOError(
                code="project_snapshot_project_missing",
                message="The snapshotted project no longer exists",
                stage="output_harvest",
                retryable=False,
            )

    def _new_artifact(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        output: RunProjectOutput,
        content: bytes,
    ) -> Artifact:
        previous = self._session.scalar(
            select(Artifact)
            .where(
                Artifact.workspace_id == run.workspace_id,
                Artifact.workspace_project_output_id == output.project_output_id,
            )
            .order_by(Artifact.version.desc(), Artifact.created_at.desc())
            .limit(1)
        )
        step = (
            self._session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if run.task_step_id is not None
            else None
        )
        filename = safe_filename(PurePosixPath(output.project_path).name, default="artifact.bin")
        artifact_id = uuid4()
        return Artifact(
            id=artifact_id,
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            agent_run_id=run.id,
            task_step_id=run.task_step_id,
            workspace_project_id=snapshot.project_id,
            workspace_project_output_id=output.project_output_id,
            agent_profile_id=run.agent_profile_id,
            supersedes_artifact_id=previous.id if previous is not None else None,
            work_package_id=step.work_package_id if step is not None else None,
            project_path=output.project_path,
            version=(previous.version + 1) if previous is not None else 1,
            review_status="pending",
            artifact_type=output.artifact_type,
            filename=filename,
            content_type=output.content_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream",
            size_bytes=len(content),
            checksum_sha256=sha256(content).hexdigest(),
            storage_key=f"workspaces/{run.workspace_id}/artifacts/{artifact_id}/{filename}",
            artifact_metadata={
                "source": "runtime_project_output",
                "project_snapshot_id": str(snapshot.id),
                "project_snapshot_fingerprint": snapshot.fingerprint_sha256,
                "project_output_id": str(output.project_output_id),
                "project_path": output.project_path,
            },
            created_at=datetime.now(UTC),
        )

    def _existing(
        self,
        run: AgentRun,
        output: RunProjectOutput,
    ) -> Artifact | None:
        return self._session.scalar(
            select(Artifact).where(
                Artifact.workspace_id == run.workspace_id,
                Artifact.agent_run_id == run.id,
                Artifact.workspace_project_output_id == output.project_output_id,
            )
        )

    def _validate_existing(
        self,
        artifact: Artifact,
        output: RunProjectOutput,
        content: bytes,
        snapshot: AgentRunProjectSnapshot,
    ) -> None:
        if (
            artifact.workspace_project_id != snapshot.project_id
            or artifact.project_path != output.project_path
            or artifact.size_bytes != len(content)
            or artifact.checksum_sha256 != sha256(content).hexdigest()
            or not self._storage.exists(artifact.storage_key)
        ):
            raise ProjectRunIOError(
                code="project_output_idempotency_conflict",
                message="An existing harvested output does not match the runtime file",
                stage="output_harvest",
                retryable=False,
                metadata={"project_output_id": str(output.project_output_id)},
            )
