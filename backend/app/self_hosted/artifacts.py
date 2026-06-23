from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import ArtifactUploadRequest, LocalFileReferenceRequest
from backend.app.files.security import safe_filename, validate_storage_key
from backend.app.runs.models import AgentRun
from backend.app.self_hosted.models import LocalFileReference, SelfHostedArtifactUpload
from backend.app.self_hosted.types import AuthenticatedWorker
from backend.app.tasks.models import Task, TaskStep


class SelfHostedArtifactService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_local_file_reference(
        self,
        auth: AuthenticatedWorker,
        data: LocalFileReferenceRequest,
    ) -> LocalFileReference:
        if data.task_id is not None:
            task = self._session.get(Task, data.task_id)
            if task is None or task.workspace_id != auth.worker.workspace_id:
                raise ValueError("Task not found")
        reference = LocalFileReference(
            workspace_id=auth.worker.workspace_id,
            workspace_runtime_id=auth.runtime.id,
            task_id=data.task_id,
            path=data.path,
            label=data.label,
            file_metadata={
                **data.metadata,
                "runtime_space_id": str(auth.runtime.runtime_space_id)
                if auth.runtime.runtime_space_id is not None
                else None,
                "workspace_runtime_id": str(auth.runtime.id),
                "worker_id": str(auth.worker.id),
            },
        )
        self._session.add(reference)
        self._session.commit()
        self._session.refresh(reference)
        return reference

    def register_artifact_upload(
        self,
        auth: AuthenticatedWorker,
        data: ArtifactUploadRequest,
    ) -> SelfHostedArtifactUpload:
        run = self._validate_worker_run(auth, data.agent_run_id)
        step = (
            self._session.get(TaskStep, run.task_step_id)
            if run is not None and run.task_step_id is not None
            else None
        )
        max_artifact_bytes = _positive_int(auth.worker.capabilities.get("max_artifact_bytes"))
        artifact_size = _positive_int(data.metadata.get("size_bytes"))
        if (
            max_artifact_bytes is not None
            and artifact_size is not None
            and artifact_size > max_artifact_bytes
        ):
            raise ValueError("Artifact upload exceeds self-hosted worker policy")
        filename = safe_filename(data.filename, default="artifact.bin")
        storage_key = (
            validate_storage_key(
                data.storage_key,
                expected_prefix=f"workspaces/{auth.worker.workspace_id}/self-hosted",
            )
            if data.storage_key is not None
            else None
        )
        upload = SelfHostedArtifactUpload(
            workspace_id=auth.worker.workspace_id,
            worker_id=auth.worker.id,
            agent_run_id=data.agent_run_id,
            filename=filename,
            storage_key=storage_key,
            checksum_sha256=data.checksum_sha256,
            artifact_metadata={
                **data.metadata,
                "task_id": str(run.task_id) if run is not None and run.task_id else None,
                "task_step_id": str(step.id) if step is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run is not None and run.agent_profile_id
                else None,
                "work_package_id": step.work_package_id if step is not None else None,
                "runtime_space_id": str(auth.runtime.runtime_space_id)
                if auth.runtime.runtime_space_id is not None
                else None,
                "workspace_runtime_id": str(auth.runtime.id),
                "worker_id": str(auth.worker.id),
            },
        )
        self._session.add(upload)
        self._session.commit()
        self._session.refresh(upload)
        return upload

    def _validate_worker_run(
        self,
        auth: AuthenticatedWorker,
        agent_run_id,
    ) -> AgentRun | None:
        if agent_run_id is None:
            return None
        run = self._session.get(AgentRun, agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Run not found for self-hosted worker")
        return run


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None
