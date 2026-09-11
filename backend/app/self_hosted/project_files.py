from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.observability.audit_service import AuditService
from backend.app.core.config import Settings
from backend.app.files.models import FileAccessEvent
from backend.app.files.storage import ObjectStorage, create_storage
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.projects.file_boundaries import (
    ProjectBoundaryViolation,
    ProjectFileBoundaryService,
)
from backend.app.projects.models import AgentRunProjectSnapshot
from backend.app.projects.output_artifacts import ProjectOutputArtifactWriter
from backend.app.projects.run_manifest import (
    RunProjectManifest,
    RunProjectOutput,
    public_run_project_manifest,
)
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.projects.runtime_io_state import ProjectIOStateService
from backend.app.projects.runtime_staging import ProjectInputArchiveBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.self_hosted.models import SelfHostedJobClaim
from backend.app.self_hosted.types import AuthenticatedWorker
from backend.app.tasks.models import Task


@dataclass(frozen=True, slots=True)
class SelfHostedProjectContract:
    root_path: str
    snapshot_id: UUID
    fingerprint_sha256: str
    manifest: dict[str, object]


@dataclass(frozen=True, slots=True)
class SelfHostedProjectArchive:
    content: bytes
    root_path: str
    fingerprint_sha256: str


class SelfHostedProjectFileService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: ObjectStorage | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._storage = storage
        self._states = ProjectIOStateService(session)

    def describe_claimed_project(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
    ) -> SelfHostedProjectContract | None:
        run = self._claimed_run(auth, agent_run_id)
        snapshot = RunProjectSnapshotService(self._session).get_for_run(
            run.workspace_id,
            run.id,
        )
        if snapshot is None:
            return None
        try:
            manifest = self._validated_manifest(run, snapshot, auth.runtime)
        except ProjectRunIOError as exc:
            state = self._states.lock_or_create(
                run,
                snapshot,
                auth.runtime,
                f"runs/{run.id}",
                stage="input_staging",
            )
            self._states.record_failure(run, state, exc)
            raise
        return SelfHostedProjectContract(
            root_path=f"runs/{run.id}",
            snapshot_id=snapshot.id,
            fingerprint_sha256=snapshot.fingerprint_sha256,
            manifest=public_run_project_manifest(manifest),
        )

    def build_input_archive(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
    ) -> SelfHostedProjectArchive | None:
        run = self._claimed_run(auth, agent_run_id, lock=True)
        snapshot = RunProjectSnapshotService(self._session).get_for_run(
            run.workspace_id,
            run.id,
        )
        if snapshot is None:
            return None
        root_path = f"runs/{run.id}"
        state = self._states.lock_or_create(
            run,
            snapshot,
            auth.runtime,
            root_path,
            stage="input_staging",
        )
        try:
            manifest = self._validated_manifest(run, snapshot, auth.runtime)
            archive = ProjectInputArchiveBuilder(self._object_storage()).build(
                run=run,
                snapshot=snapshot,
                manifest=manifest,
            )
        except ProjectRunIOError as exc:
            self._states.record_failure(run, state, exc)
            raise
        except Exception as exc:
            error = ProjectRunIOError(
                code="project_input_staging_failed",
                message="Project inputs could not be prepared for the self-hosted runtime",
                stage="input_staging",
                retryable=True,
            )
            self._states.record_failure(run, state, error)
            raise error from exc
        if state.status not in {"staged", "harvested"}:
            self._states.mark_inputs_staged(
                run=run,
                state=state,
                snapshot=snapshot,
                runtime=auth.runtime,
                manifest=manifest,
                file_count=archive.file_count,
                total_bytes=archive.total_bytes,
                actor_user_id=None,
            )
        return SelfHostedProjectArchive(
            content=archive.content,
            root_path=root_path,
            fingerprint_sha256=snapshot.fingerprint_sha256,
        )

    def output_limit(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        project_output_id: UUID,
    ) -> int:
        run = self._claimed_run(auth, agent_run_id)
        _, output = self._declared_output(
            run,
            auth.runtime,
            project_output_id,
            lock=True,
        )
        return output.max_bytes

    def upload_output(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        project_output_id: UUID,
        *,
        content: bytes,
        content_type: str | None,
    ) -> Artifact:
        run = self._claimed_run(auth, agent_run_id, lock=True)
        snapshot, output = self._declared_output(
            run,
            auth.runtime,
            project_output_id,
            lock=True,
        )
        self._validate_output_payload(output, content, content_type)
        try:
            prepared = ProjectOutputArtifactWriter(
                self._session,
                self._object_storage(),
            ).prepare(run=run, snapshot=snapshot, outputs=[(output, content)])
        except ProjectRunIOError:
            raise
        except Exception as exc:
            raise ProjectRunIOError(
                code="project_output_storage_unavailable",
                message="The project output could not be persisted",
                stage="output_harvest",
                retryable=True,
                metadata={"project_output_id": str(project_output_id)},
            ) from exc
        artifact = prepared.artifacts[0]
        try:
            if artifact in prepared.new_artifacts:
                self._record_output_upload(run, snapshot.id, output, artifact)
        except Exception:
            prepared.abort()
            raise
        prepared.commit()
        return artifact

    def finalize_outputs(self, run: AgentRun) -> list[Artifact]:
        snapshot = RunProjectSnapshotService(self._session).get_for_run(
            run.workspace_id,
            run.id,
        )
        if snapshot is None:
            return []
        state = self._states.locked(run)
        if state is None or state.status not in {"staged", "harvested"}:
            raise ProjectRunIOError(
                code="project_outputs_not_ready",
                message="The run project archive was not staged by this worker",
                stage="output_harvest",
                retryable=False,
            )
        artifacts = self._states.harvested_artifacts(run)
        if state.status == "harvested":
            return artifacts
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == state.workspace_runtime_id,
            )
        )
        if runtime is None:
            error = ProjectRunIOError(
                code="project_runtime_unavailable",
                message="The authorized project runtime is unavailable",
                stage="output_harvest",
                retryable=False,
                metadata={"boundary_denial": True},
            )
            self._states.record_failure(run, state, error)
            raise error
        try:
            manifest = self._states.manifest(snapshot, run, stage="output_harvest")
            self._validate_boundary(
                run,
                snapshot,
                runtime,
                manifest,
                stage="output_harvest",
            )
        except ProjectRunIOError as exc:
            self._states.record_failure(run, state, exc)
            raise
        artifact_output_ids = {
            artifact.workspace_project_output_id for artifact in artifacts
        }
        missing = [
            str(output.project_output_id)
            for output in manifest.outputs
            if output.required and output.project_output_id not in artifact_output_ids
        ]
        if missing:
            raise ProjectRunIOError(
                code="project_required_output_missing",
                message="One or more required project outputs were not uploaded",
                stage="output_harvest",
                retryable=False,
                metadata={"project_output_ids": missing},
            )
        self._states.mark_outputs_harvested(run, snapshot, state, artifacts)
        return artifacts

    def _declared_output(
        self,
        run: AgentRun,
        runtime: WorkspaceRuntime,
        project_output_id: UUID,
        *,
        lock: bool,
    ) -> tuple[AgentRunProjectSnapshot, RunProjectOutput]:
        state = self._states.locked(run) if lock else self._states.get(run)
        if state is None or state.status != "staged":
            raise ProjectRunIOError(
                code="project_outputs_not_ready",
                message="Project outputs cannot be uploaded before inputs are staged",
                stage="output_harvest",
                retryable=False,
            )
        snapshot = self._states.snapshot_for_state(run, state, stage="output_harvest")
        try:
            manifest = self._states.manifest(snapshot, run, stage="output_harvest")
            self._validate_boundary(
                run,
                snapshot,
                runtime,
                manifest,
                stage="output_harvest",
            )
        except ProjectRunIOError as exc:
            self._states.record_failure(run, state, exc)
            raise
        output = next(
            (item for item in manifest.outputs if item.project_output_id == project_output_id),
            None,
        )
        if output is None:
            raise ProjectRunIOError(
                code="project_output_not_declared",
                message="The project output is not declared by this run snapshot",
                stage="output_harvest",
                retryable=False,
            )
        return snapshot, output

    @staticmethod
    def _validate_output_payload(
        output: RunProjectOutput,
        content: bytes,
        content_type: str | None,
    ) -> None:
        if len(content) > output.max_bytes:
            raise ProjectRunIOError(
                code="project_output_too_large",
                message="The project output exceeds its declared size limit",
                stage="output_harvest",
                retryable=False,
                metadata={"project_output_id": str(output.project_output_id)},
            )
        if output.content_type is not None and output.content_type != content_type:
            raise ProjectRunIOError(
                code="project_output_content_type_mismatch",
                message="The project output content type does not match its declaration",
                stage="output_harvest",
                retryable=False,
                metadata={"project_output_id": str(output.project_output_id)},
            )

    def _record_output_upload(
        self,
        run: AgentRun,
        snapshot_id: UUID,
        output: RunProjectOutput,
        artifact: Artifact,
    ) -> None:
        now = datetime.now(UTC)
        self._session.add(
            FileAccessEvent(
                workspace_id=run.workspace_id,
                workspace_file_id=None,
                artifact_id=artifact.id,
                user_id=None,
                action="upload_from_self_hosted_runtime",
                created_at=now,
            )
        )
        metadata: dict[str, object] = {
            "project_snapshot_id": str(snapshot_id),
            "project_output_id": str(output.project_output_id),
            "artifact_id": str(artifact.id),
            "size_bytes": artifact.size_bytes,
        }
        RunEventRecorder(self._session).append_event(
            run,
            "project.output_uploaded",
            "A declared output was uploaded by the self-hosted runtime",
            metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="project.output_uploaded",
            target_type="artifact",
            target_id=artifact.id,
            metadata={"agent_run_id": str(run.id), **metadata},
        )

    def _claimed_run(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        *,
        lock: bool = False,
    ) -> AgentRun:
        query = (
            select(AgentRun)
            .join(
                SelfHostedJobClaim,
                SelfHostedJobClaim.agent_run_id == AgentRun.id,
            )
            .where(
                AgentRun.id == agent_run_id,
                AgentRun.workspace_id == auth.worker.workspace_id,
                AgentRun.runtime_id == auth.runtime.id,
                AgentRun.status == "running",
                SelfHostedJobClaim.workspace_id == auth.worker.workspace_id,
                SelfHostedJobClaim.worker_id == auth.worker.id,
                SelfHostedJobClaim.status == "claimed",
            )
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        run = self._session.scalar(query)
        if run is None:
            raise ValueError("Active self-hosted job not found for this worker")
        return run

    def _object_storage(self) -> ObjectStorage:
        if self._storage is None:
            self._storage = create_storage(self._settings)
        return self._storage

    def _validated_manifest(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        runtime: WorkspaceRuntime,
    ) -> RunProjectManifest:
        manifest = self._states.manifest(snapshot, run, stage="input_staging")
        self._validate_boundary(
            run,
            snapshot,
            runtime,
            manifest,
            stage="input_staging",
        )
        return manifest

    def _validate_boundary(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        runtime: WorkspaceRuntime,
        manifest: RunProjectManifest,
        *,
        stage: str,
    ) -> None:
        if run.task_id is None:
            error = ProjectRunIOError(
                code="project_task_missing",
                message="Project run task is unavailable",
                stage=stage,
                retryable=False,
                metadata={"boundary_denial": True},
            )
            raise error
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
        )
        if task is None:
            error = ProjectRunIOError(
                code="project_task_missing",
                message="Project run task is unavailable",
                stage=stage,
                retryable=False,
                metadata={"boundary_denial": True},
            )
            raise error
        boundaries = ProjectFileBoundaryService(self._session)
        try:
            boundaries.validate_runtime_io(
                run=run,
                task=task,
                runtime=runtime,
                project_snapshot=snapshot,
                manifest=manifest,
            )
        except ProjectBoundaryViolation as exc:
            error = boundaries.as_io_error(exc, stage=stage)
            raise error from exc
