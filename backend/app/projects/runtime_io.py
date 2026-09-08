from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.core.config import Settings
from backend.app.files.models import FileAccessEvent
from backend.app.files.storage import ObjectStorage, create_storage
from backend.app.projects.models import AgentRunProjectIOState, AgentRunProjectSnapshot
from backend.app.projects.output_artifacts import ProjectOutputArtifactWriter
from backend.app.projects.run_manifest import RunProjectManifest
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.projects.runtime_io_state import ProjectIOStateService
from backend.app.projects.runtime_staging import ProjectInputArchiveBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.backends import build_runtime_backend_registry
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeProjectFilesystem
from backend.app.runtimes.models import WorkspaceRuntime


@dataclass(frozen=True, slots=True)
class ProjectStageResult:
    state_id: UUID
    root_path: str
    file_count: int
    total_bytes: int


class RunProjectIOService:
    def __init__(
        self,
        session: Session,
        storage: ObjectStorage | None,
        docker_client: DockerRuntimeClient | None,
        settings: Settings,
    ) -> None:
        self._session = session
        self._storage = storage
        self._docker_client = docker_client
        self._settings = settings
        self._states = ProjectIOStateService(session)

    def stage_inputs(
        self,
        run: AgentRun,
        *,
        actor_user_id: UUID | None,
    ) -> ProjectStageResult | None:
        snapshot = RunProjectSnapshotService(self._session).get_for_run(
            run.workspace_id,
            run.id,
        )
        if snapshot is None:
            return None
        if run.runtime_id is None:
            raise ProjectRunIOError(
                code="project_runtime_required",
                message="A run with a project snapshot requires an authorized file runtime",
                stage="input_staging",
                retryable=False,
            )
        resolved = self._runtime_filesystem(run, stage="input_staging")
        runtime, filesystem = resolved
        state = self._states.lock_or_create(
            run,
            snapshot,
            runtime,
            filesystem.root_path,
            stage="input_staging",
        )
        if state.status in {"staged", "harvesting", "harvested"}:
            return self._stage_result(state)

        try:
            manifest = self._states.manifest(snapshot, run, stage="input_staging")
            archive = ProjectInputArchiveBuilder(self._object_storage()).build(
                run=run,
                snapshot=snapshot,
                manifest=manifest,
            )
            filesystem.stage_archive(archive.content)
        except ProjectRunIOError as exc:
            self._states.record_failure(run, state, exc)
            raise
        except Exception as exc:
            error = ProjectRunIOError(
                code="project_input_staging_failed",
                message="Project inputs could not be staged into the runtime",
                stage="input_staging",
                retryable=True,
            )
            self._states.record_failure(run, state, error)
            raise error from exc

        self._states.mark_inputs_staged(
            run=run,
            state=state,
            snapshot=snapshot,
            runtime=runtime,
            manifest=manifest,
            file_count=archive.file_count,
            total_bytes=archive.total_bytes,
            actor_user_id=actor_user_id,
        )
        return self._stage_result(state)

    def harvest_outputs(
        self,
        run: AgentRun,
        *,
        actor_user_id: UUID | None,
    ) -> list[Artifact]:
        state = self._states.locked(run)
        if state is None:
            return []
        snapshot = self._states.snapshot_for_state(run, state, stage="output_harvest")
        manifest = self._states.manifest(snapshot, run, stage="output_harvest")
        if state.status == "harvested":
            return self._states.harvested_artifacts(run)
        if state.status != "staged":
            raise ProjectRunIOError(
                code="project_outputs_not_ready",
                message="Project outputs cannot be harvested before inputs are staged",
                stage="output_harvest",
                retryable=False,
            )
        resolved = self._runtime_filesystem(run, stage="output_harvest")
        _, filesystem = resolved
        state.status = "harvesting"
        try:
            output_contents = self._read_declared_outputs(run, manifest, filesystem)
            return self._persist_harvested_outputs(
                run=run,
                snapshot=snapshot,
                state=state,
                manifest=manifest,
                output_contents=output_contents,
                actor_user_id=actor_user_id,
            )
        except ProjectRunIOError as exc:
            self._session.rollback()
            locked_state = self._states.locked(run)
            if locked_state is not None:
                self._states.record_failure(run, locked_state, exc)
            raise
        except Exception as exc:
            self._session.rollback()
            error = ProjectRunIOError(
                code="project_output_harvest_failed",
                message="Declared project outputs could not be harvested",
                stage="output_harvest",
                retryable=True,
            )
            locked_state = self._states.locked(run)
            if locked_state is not None:
                self._states.record_failure(run, locked_state, error)
            raise error from exc

    def _read_declared_outputs(
        self,
        run: AgentRun,
        manifest: RunProjectManifest,
        filesystem: RuntimeProjectFilesystem,
    ) -> dict[UUID, bytes]:
        contents: dict[UUID, bytes] = {}
        for output in manifest.outputs:
            try:
                content = filesystem.read_file(output.project_path, max_bytes=output.max_bytes)
            except ValueError as exc:
                raise ProjectRunIOError(
                    code="project_output_policy_failed",
                    message="A declared project output violates its file policy",
                    stage="output_harvest",
                    retryable=False,
                    metadata={"project_output_id": str(output.project_output_id)},
                ) from exc
            except Exception as exc:
                raise ProjectRunIOError(
                    code="project_output_runtime_unavailable",
                    message="The runtime could not return a declared project output",
                    stage="output_harvest",
                    retryable=True,
                    metadata={"project_output_id": str(output.project_output_id)},
                ) from exc
            if content is None:
                if output.required:
                    raise ProjectRunIOError(
                        code="project_required_output_missing",
                        message="A required project output was not produced",
                        stage="output_harvest",
                        retryable=False,
                        metadata={"project_output_id": str(output.project_output_id)},
                    )
                continue
            contents[output.project_output_id] = content
        return contents

    def _persist_harvested_outputs(
        self,
        *,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        state: AgentRunProjectIOState,
        manifest: RunProjectManifest,
        output_contents: dict[UUID, bytes],
        actor_user_id: UUID | None,
    ) -> list[Artifact]:
        outputs = [
            (output, content)
            for output in manifest.outputs
            if (content := output_contents.get(output.project_output_id)) is not None
        ]
        prepared = ProjectOutputArtifactWriter(
            self._session,
            self._object_storage(),
        ).prepare(run=run, snapshot=snapshot, outputs=outputs)
        try:
            now = datetime.now(UTC)
            for artifact in prepared.new_artifacts:
                self._session.add(
                    FileAccessEvent(
                        workspace_id=run.workspace_id,
                        workspace_file_id=None,
                        artifact_id=artifact.id,
                        user_id=actor_user_id,
                        action="collect_from_runtime",
                        created_at=now,
                    )
                )
            self._states.mark_outputs_harvested(
                run,
                snapshot,
                state,
                prepared.artifacts,
            )
        except Exception:
            prepared.abort()
            raise
        prepared.commit()
        return prepared.artifacts

    def _runtime_filesystem(
        self,
        run: AgentRun,
        *,
        stage: str,
    ) -> tuple[WorkspaceRuntime, RuntimeProjectFilesystem]:
        if run.runtime_id is None:
            raise ProjectRunIOError(
                code="project_runtime_required",
                message="A run with a project snapshot requires an authorized file runtime",
                stage=stage,
                retryable=False,
            )
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == run.runtime_id,
                WorkspaceRuntime.status.in_(("active", "running")),
                WorkspaceRuntime.connection_status == "online",
            )
        )
        if runtime is None:
            raise ProjectRunIOError(
                code="project_runtime_unavailable",
                message="The authorized project runtime is unavailable",
                stage=stage,
                retryable=True,
            )
        backend = build_runtime_backend_registry(
            self._session,
            self._docker_client,
            None,
        ).resolve(runtime.runtime_provider)
        if backend is None:
            raise ProjectRunIOError(
                code="project_runtime_provider_unsupported",
                message="The runtime provider does not support project files",
                stage=stage,
                retryable=False,
            )
        try:
            filesystem = backend.project_filesystem(runtime, run.id)
        except ProjectRunIOError:
            raise
        except Exception as exc:
            raise ProjectRunIOError(
                code="project_runtime_files_unavailable",
                message="The authorized runtime does not expose project files",
                stage=stage,
                retryable=True,
            ) from exc
        if filesystem is None:
            raise ProjectRunIOError(
                code="project_runtime_files_unavailable",
                message="The authorized runtime does not expose project files",
                stage=stage,
                retryable=False,
            )
        return runtime, filesystem

    def _object_storage(self) -> ObjectStorage:
        if self._storage is None:
            self._storage = create_storage(self._settings)
        return self._storage

    @staticmethod
    def _stage_result(state: AgentRunProjectIOState) -> ProjectStageResult:
        return ProjectStageResult(
            state_id=state.id,
            root_path=state.root_path,
            file_count=state.staged_file_count,
            total_bytes=state.staged_bytes,
        )
