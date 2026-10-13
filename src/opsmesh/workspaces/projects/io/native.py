"""Project file governance through public native SDK manifest and session extensions."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

from agents.sandbox.entries import BaseEntry, Dir, File
from agents.sandbox.errors import WorkspaceReadNotFoundError
from agents.sandbox.files import EntryKind
from agents.sandbox.materialization import MaterializedFile
from agents.sandbox.session import BaseSandboxSession
from agents.sandbox.types import Permissions
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry, native_resource_identity
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.workspaces.projects.artifacts import ProjectOutputArtifactWriter
from opsmesh.workspaces.projects.file_boundaries import (
    ProjectBoundaryViolationError,
    ProjectFileBoundaryService,
)
from opsmesh.workspaces.projects.io.state import ProjectIOStateService
from opsmesh.workspaces.projects.io.support import RunProjectIOError
from opsmesh.workspaces.projects.models import AgentRunProjectSnapshot
from opsmesh.workspaces.projects.snapshots.manifest import (
    RunProjectManifest,
    RunProjectOutput,
    parse_run_project_manifest,
)
from opsmesh.workspaces.projects.snapshots.service import RunProjectSnapshotService

PROJECT_FILES_DEPENDENCY = "opsmesh.project.files"


class ProjectOutputHarvestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    run_id: UUID
    backend_id: str
    session_state: dict[str, object]


def require_workflow_scope(workspace_id: UUID, run_id: UUID) -> None:
    if activity.info().workflow_id != f"opsmesh:{workspace_id}:agent:{run_id}":
        raise ValueError("Project files require their authorized Agent workflow")


class ProjectInputEntry(BaseEntry):
    type: Literal["opsmesh_project_inputs"] = "opsmesh_project_inputs"
    is_dir: bool = True
    workspace_id: UUID
    run_id: UUID

    async def apply(
        self, session: BaseSandboxSession, dest: Path, base_dir: Path
    ) -> list[MaterializedFile]:
        require_workflow_scope(self.workspace_id, self.run_id)
        handler = await session.dependencies.require(
            PROJECT_FILES_DEPENDENCY, consumer="Project files"
        )
        if not isinstance(handler, ProjectSandboxFileService):
            raise TypeError("Native project file dependency is unavailable")
        await handler.stage(session, self.workspace_id, self.run_id)
        return []


class ProjectSandboxFileService:
    """The SDK owns copying and workspace cleanup; this class owns product file grants."""

    def __init__(self, services: ActivityDependencies, storage: ObjectStorage) -> None:
        self._services = services
        self._storage = storage

    @staticmethod
    def load(
        session: Session,
        workspace_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentRun, WorkspaceRuntime, AgentRunProjectSnapshot, RunProjectManifest] | None:
        run = session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id == run_id,
            )
        )
        if run is None or run.status != "running":
            raise ValueError("Project run is unavailable")
        snapshot = RunProjectSnapshotService(session).get_for_run(workspace_id, run_id)
        if snapshot is None:
            return None
        runtime = session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == run.runtime_id,
                WorkspaceRuntime.status == "active",
            )
        )
        task = session.scalar(
            select(Task).where(
                Task.workspace_id == workspace_id,
                Task.id == run.task_id,
            )
        )
        if runtime is None or task is None:
            raise ValueError("Project execution environment is unavailable")
        try:
            manifest = parse_run_project_manifest(snapshot, workspace_id=workspace_id)
        except ValueError as exc:
            raise ProjectBoundaryViolationError(
                code="project_snapshot_invalid",
                message="Project snapshot failed integrity validation",
                metadata={},
            ) from exc
        ProjectFileBoundaryService(session).validate_runtime_io(
            run=run,
            task=task,
            runtime=runtime,
            project_snapshot=snapshot,
            manifest=manifest,
        )
        return run, runtime, snapshot, manifest

    async def stage(self, native: BaseSandboxSession, workspace_id: UUID, run_id: UUID) -> None:
        require_workflow_scope(workspace_id, run_id)
        try:
            await self._stage(native, workspace_id, run_id)
        except ProjectBoundaryViolationError as violation:
            error = ProjectFileBoundaryService.as_io_error(violation, stage="input_staging")
            await asyncio.to_thread(
                self.record_failure, workspace_id, run_id, str(native.state.manifest.root), error
            )
            raise error from violation
        except RunProjectIOError as error:
            await asyncio.to_thread(
                self.record_failure, workspace_id, run_id, str(native.state.manifest.root), error
            )
            raise

    def record_failure(
        self, workspace_id: UUID, run_id: UUID, root: str, error: RunProjectIOError
    ) -> None:
        with self._services.transaction() as session:
            run = session.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.id == run_id,
                )
            )
            snapshot = RunProjectSnapshotService(session).get_for_run(workspace_id, run_id)
            if run is None or snapshot is None or run.runtime_id is None:
                return
            runtime = session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id == run.runtime_id,
                )
            )
            if runtime is None:
                return
            states = ProjectIOStateService(session)
            state = states.lock_or_create(
                run, snapshot, runtime, root, stage=str(error.metadata["stage"])
            )
            states.record_failure(run, state, error)

    async def _stage(self, native: BaseSandboxSession, workspace_id: UUID, run_id: UUID) -> None:

        def prepare() -> tuple[RunProjectManifest, dict[str, bytes]] | None:
            with self._services.transaction() as session:
                loaded = self.load(session, workspace_id, run_id)
                if loaded is None:
                    return None
                _, _, _, manifest = loaded
            return manifest, self._read_inputs(manifest)

        prepared = await asyncio.to_thread(prepare)
        if prepared is None:
            return
        manifest, contents = prepared
        root = Path(native.state.manifest.root)
        for path in (manifest.input_path, manifest.work_path, manifest.output_path):
            await Dir().apply(native, root / path, root)
        for item in manifest.files:
            await native.mkdir((root / item.project_path).parent, parents=True)
            await File(
                content=contents[item.project_path],
                permissions=Permissions.from_mode(
                    0o444 if item.access_mode == "read_only" else 0o644
                ),
            ).apply(native, root / item.project_path, root)

        def persist() -> None:
            with self._services.transaction() as session:
                loaded = self.load(session, workspace_id, run_id)
                if loaded is None:
                    raise ValueError("Project snapshot disappeared during input staging")
                run, runtime, snapshot, manifest = loaded
                states = ProjectIOStateService(session)
                state = states.lock_or_create(
                    run, snapshot, runtime, str(root), stage="input_staging"
                )
                if state.status not in {"staged", "harvested"}:
                    states.mark_inputs_staged(
                        run=run,
                        state=state,
                        snapshot=snapshot,
                        runtime=runtime,
                        manifest=manifest,
                        file_count=len(contents),
                        total_bytes=sum(map(len, contents.values())),
                        actor_user_id=None,
                    )

        await asyncio.to_thread(persist)

    def _read_inputs(self, manifest: RunProjectManifest) -> dict[str, bytes]:
        contents: dict[str, bytes] = {}
        for item in manifest.files:
            content = self._storage.read_limited(item.storage_key, item.size_bytes)
            if (
                len(content) != item.size_bytes
                or sha256(content).hexdigest() != item.checksum_sha256
            ):
                raise RunProjectIOError(
                    code="project_input_integrity_failed",
                    message="A snapshotted project input failed integrity verification",
                    stage="input_staging",
                    retryable=False,
                )
            contents[item.project_path] = content
        return contents


class ProjectOutputActivities:
    def __init__(
        self,
        services: ActivityDependencies,
        backends: SandboxBackendRegistry,
        storage: ObjectStorage,
    ) -> None:
        self._services = services
        self._backends = backends
        self._storage = storage

    @activity.defn(name="opsmesh.project.harvest")
    async def harvest(self, operation: ProjectOutputHarvestRequest) -> None:
        require_workflow_scope(operation.workspace_id, operation.run_id)
        try:
            await self._harvest(operation)
        except ProjectBoundaryViolationError as violation:
            error = ProjectFileBoundaryService.as_io_error(violation, stage="output_harvest")
            await asyncio.to_thread(
                ProjectSandboxFileService(self._services, self._storage).record_failure,
                operation.workspace_id,
                operation.run_id,
                "/workspace",
                error,
            )
            raise error from violation
        except RunProjectIOError as error:
            await asyncio.to_thread(
                ProjectSandboxFileService(self._services, self._storage).record_failure,
                operation.workspace_id,
                operation.run_id,
                "/workspace",
                error,
            )
            raise

    async def _harvest(self, operation: ProjectOutputHarvestRequest) -> None:

        def authorize() -> RunProjectManifest | None:
            with self._services.transaction() as session:
                loaded = ProjectSandboxFileService.load(
                    session, operation.workspace_id, operation.run_id
                )
                if loaded is None:
                    return None
                _, runtime, _, manifest = loaded
                if runtime.sandbox_backend_id != operation.backend_id:
                    raise ValueError("Project output sandbox is outside the authorized environment")
                return manifest

        manifest = await asyncio.to_thread(authorize)
        if manifest is None:
            return
        backend = self._backends.require(operation.backend_id)
        supplied_state = backend.client.deserialize_session_state(operation.session_state)
        original_resource = native_resource_identity(supplied_state)
        native = await backend.client.resume(supplied_state)
        try:
            entries = native.state.manifest.entries.values()
            if not any(
                isinstance(entry, ProjectInputEntry)
                and entry.workspace_id == operation.workspace_id
                and entry.run_id == operation.run_id
                for entry in entries
            ):
                raise ValueError("Project output sandbox has no authorized project manifest")
            # start() restores the SDK snapshot committed before final harvest.
            await native.start()
            outputs = await self._collect_outputs(native, manifest)
        finally:
            if native_resource_identity(native.state) != original_resource:
                # Only a provider-created replacement belongs to this Activity.
                await asyncio.shield(backend.client.delete(native))
        root = Path(native.state.manifest.root)

        def persist() -> None:
            with self._services.transaction() as session:
                loaded = ProjectSandboxFileService.load(
                    session, operation.workspace_id, operation.run_id
                )
                if loaded is None:
                    raise ValueError("Project snapshot disappeared during output collection")
                run, runtime, snapshot, _ = loaded
                states = ProjectIOStateService(session)
                state = states.lock_or_create(
                    run, snapshot, runtime, str(root), stage="output_harvest"
                )
                prepared = ProjectOutputArtifactWriter(session, self._storage).prepare(
                    run=run,
                    snapshot=snapshot,
                    outputs=outputs,
                )
                try:
                    states.mark_outputs_harvested(run, snapshot, state, prepared.artifacts)
                    prepared.commit()
                except BaseException:
                    prepared.abort()
                    raise

        await asyncio.to_thread(persist)

    async def _collect_outputs(
        self, native: BaseSandboxSession, manifest: RunProjectManifest
    ) -> list[tuple[RunProjectOutput, bytes]]:
        root = Path(native.state.manifest.root)
        outputs = []
        for output in manifest.outputs:
            try:
                presence = await native.exec(
                    "test",
                    "-e",
                    root / output.project_path,
                    shell=False,
                )
                if presence.exit_code == 1:
                    if output.required:
                        raise RunProjectIOError(
                            code="project_required_output_missing",
                            message="A required declared project output is missing",
                            stage="output_harvest",
                            retryable=False,
                        )
                    continue
                if not presence.ok():
                    raise ValueError("Project output presence could not be determined")
                output_entries = await native.ls(root / output.project_path)
                if len(output_entries) != 1 or output_entries[0].kind != EntryKind.FILE:
                    raise RunProjectIOError(
                        code="project_output_not_regular_file",
                        message="Declared project outputs must be regular files",
                        stage="output_harvest",
                        retryable=False,
                    )
                if output_entries[0].size > output.max_bytes:
                    raise RunProjectIOError(
                        code="project_output_size_exceeded",
                        message="A declared project output exceeds its transfer limit",
                        stage="output_harvest",
                        retryable=False,
                    )
                with await native.read(root / output.project_path) as stream:
                    content = await asyncio.to_thread(stream.read, output.max_bytes + 1)
            except WorkspaceReadNotFoundError:
                if output.required:
                    raise RunProjectIOError(
                        code="project_required_output_missing",
                        message="A required declared project output is missing",
                        stage="output_harvest",
                        retryable=False,
                    ) from None
                continue
            if not isinstance(content, bytes) or len(content) > output.max_bytes:
                raise RunProjectIOError(
                    code="project_output_size_exceeded",
                    message="A declared project output exceeds its transfer limit",
                    stage="output_harvest",
                    retryable=False,
                )
            outputs.append((output, content))

        return outputs
