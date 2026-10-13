from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.authorization.policy import RunRuntimeAuthorizationError
from opsmesh.orchestration.runs.authorization.runtime import runtime_binding_for_snapshot
from opsmesh.orchestration.runs.authorization.validation import (
    RunAuthorizationService,
    authorized_profile_for_run,
)
from opsmesh.orchestration.runs.models import (
    AUTHORIZATION_SNAPSHOT_VERSION,
    AgentRun,
    authorization_snapshot_fingerprint,
)
from opsmesh.orchestration.tasks.models import Task
from opsmesh.platform.settings.configuration import operational_configuration
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.files.runtime_policy import runtime_file_denial_code
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.workspaces.projects.io.support import RunProjectIOError
from opsmesh.workspaces.projects.models import AgentRunProjectSnapshot
from opsmesh.workspaces.projects.snapshots.manifest import RunProjectManifest


@dataclass
class ProjectBoundaryViolationError(ValueError):
    code: str
    message: str
    metadata: dict[str, object]

    def __str__(self) -> str:
        return self.message


class ProjectFileBoundaryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def validate_runtime_io(
        self,
        *,
        run: AgentRun,
        task: Task,
        runtime: WorkspaceRuntime,
        project_snapshot: AgentRunProjectSnapshot,
        manifest: RunProjectManifest,
    ) -> None:
        try:
            self._validate_frozen_scope(run=run, task=task, manifest=manifest)
            self._validate_project_snapshot_binding(run, project_snapshot)
            self._validate_manifest_capacity(manifest)
            snapshot = _authorization_snapshot(run)
            RunAuthorizationService(self._session).validate_authorization_snapshot(
                run,
                task,
                authorized_profile_for_run(self._session, run),
                snapshot,
                lock_resources=True,
            )
            self._validate_current_files(run, manifest)
        except ProjectBoundaryViolationError:
            raise
        except RunRuntimeAuthorizationError as exc:
            raise ProjectBoundaryViolationError(
                code="project_input_authorization_denied",
                message="Project inputs are not authorized for this runtime",
                metadata={"authorization_reason": exc.code},
            ) from exc
        except ValueError as exc:
            raise ProjectBoundaryViolationError(
                code="project_input_authorization_invalid",
                message="Project input authorization failed integrity validation",
                metadata={},
            ) from exc

    @staticmethod
    def as_io_error(
        violation: ProjectBoundaryViolationError,
        *,
        stage: str,
    ) -> RunProjectIOError:
        return RunProjectIOError(
            code=violation.code,
            message=violation.message,
            stage=stage,
            retryable=False,
            metadata={"boundary_denial": True, **violation.metadata},
        )

    @staticmethod
    def _validate_project_snapshot_binding(
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
    ) -> None:
        run_input = run.input if isinstance(run.input, dict) else {}
        binding = run_input.get("project_snapshot")
        if not isinstance(binding, dict) or binding != {
            "id": str(snapshot.id),
            "project_id": str(snapshot.project_id),
            "schema_version": snapshot.schema_version,
            "fingerprint_sha256": snapshot.fingerprint_sha256,
        }:
            raise ProjectBoundaryViolationError(
                "project_snapshot_binding_invalid",
                "Project snapshot binding failed integrity validation",
                {},
            )

    @staticmethod
    def _validate_frozen_scope(
        *,
        run: AgentRun,
        task: Task,
        manifest: RunProjectManifest,
    ) -> None:
        if run.workspace_id != task.workspace_id or run.task_id != task.id:
            raise ProjectBoundaryViolationError(
                "project_input_scope_denied",
                "Project input scope does not match the run task",
                {},
            )
        snapshot = _authorization_snapshot(run)
        if snapshot.get("version") != AUTHORIZATION_SNAPSHOT_VERSION:
            raise ProjectBoundaryViolationError(
                "project_input_authorization_invalid",
                "Project input authorization snapshot is invalid",
                {},
            )
        fingerprint = snapshot.get("fingerprint")
        if not isinstance(fingerprint, str) or fingerprint != authorization_snapshot_fingerprint(
            snapshot
        ):
            raise ProjectBoundaryViolationError(
                "project_input_authorization_invalid",
                "Project input authorization snapshot failed integrity validation",
                {},
            )
        if snapshot.get("workspace_id") != str(run.workspace_id) or snapshot.get("task_id") != str(
            task.id
        ):
            raise ProjectBoundaryViolationError(
                "project_input_scope_denied",
                "Project input authorization scope does not match the run",
                {},
            )
        file_scope = snapshot.get("file_scope")
        if (
            not isinstance(file_scope, dict)
            or file_scope.get("mode") != "authorized_file_resources"
            or file_scope.get("workspace_id") != str(run.workspace_id)
            or file_scope.get("task_id") != str(task.id)
        ):
            raise ProjectBoundaryViolationError(
                "project_input_scope_denied",
                "Project input file scope is invalid",
                {},
            )
        allowed_file_ids = _uuid_set(file_scope.get("allowed_file_ids"))
        binding = runtime_binding_for_snapshot(snapshot, workspace_id=run.workspace_id)
        if (
            binding is None
            or binding.workspace_runtime_id != run.runtime_id
            or set(binding.allowed_file_ids) != allowed_file_ids
        ):
            raise ProjectBoundaryViolationError(
                "project_input_scope_denied",
                "Project input runtime binding is invalid",
                {},
            )
        manifest_file_ids = {item.workspace_file_id for item in manifest.files}
        missing_ids = manifest_file_ids - allowed_file_ids
        if missing_ids:
            raise ProjectBoundaryViolationError(
                "project_input_grant_denied",
                "One or more project inputs are outside the authorized file scope",
                {"denied_file_count": len(missing_ids)},
            )

    def _validate_manifest_capacity(self, manifest: RunProjectManifest) -> None:
        input_bytes = sum(item.size_bytes for item in manifest.files)
        output_bytes = sum(item.max_bytes for item in manifest.outputs)
        if len(manifest.files) > operational_configuration(self._session).files.input_files:
            raise ProjectBoundaryViolationError(
                "project_input_file_limit_exceeded",
                "Project input count exceeds the runtime staging limit",
                {
                    "file_count": len(manifest.files),
                    "limit": operational_configuration(self._session).files.input_files,
                },
            )
        if input_bytes > operational_configuration(self._session).files.input_bytes:
            raise ProjectBoundaryViolationError(
                "project_input_byte_limit_exceeded",
                "Project inputs exceed the runtime staging byte limit",
                {
                    "input_bytes": input_bytes,
                    "limit": operational_configuration(self._session).files.input_bytes,
                },
            )
        if len(manifest.outputs) > operational_configuration(self._session).files.output_files:
            raise ProjectBoundaryViolationError(
                "project_output_count_limit_exceeded",
                "Project output count exceeds the runtime collection limit",
                {
                    "output_count": len(manifest.outputs),
                    "limit": operational_configuration(self._session).files.output_files,
                },
            )
        if output_bytes > operational_configuration(self._session).files.output_bytes:
            raise ProjectBoundaryViolationError(
                "project_output_byte_limit_exceeded",
                "Declared project outputs exceed the runtime collection byte limit",
                {
                    "output_bytes": output_bytes,
                    "limit": operational_configuration(self._session).files.output_bytes,
                },
            )

    def _validate_current_files(
        self,
        run: AgentRun,
        manifest: RunProjectManifest,
    ) -> None:
        if not manifest.files:
            return
        files = list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(
                    WorkspaceFile.workspace_id == run.workspace_id,
                    WorkspaceFile.id.in_([item.workspace_file_id for item in manifest.files]),
                    WorkspaceFile.status == "active",
                )
                .with_for_update()
            )
        )
        by_id = {file.id: file for file in files}
        for item in manifest.files:
            file = by_id.get(item.workspace_file_id)
            if file is None:
                raise ProjectBoundaryViolationError(
                    "project_input_unavailable",
                    "An authorized project input is unavailable",
                    {"project_file_id": str(item.project_file_id)},
                )
            if (
                file.size_bytes != item.size_bytes
                or file.checksum_sha256 != item.checksum_sha256
                or file.storage_key != item.storage_key
            ):
                raise ProjectBoundaryViolationError(
                    "project_input_snapshot_mismatch",
                    "An authorized project input no longer matches its snapshot",
                    {"project_file_id": str(item.project_file_id)},
                )
            denial_code = runtime_file_denial_code(file)
            if denial_code is not None:
                raise ProjectBoundaryViolationError(
                    denial_code,
                    "A project input is blocked by the workspace file runtime policy",
                    {"project_file_id": str(item.project_file_id)},
                )


def _authorization_snapshot(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    if not isinstance(snapshot, dict):
        raise ProjectBoundaryViolationError(
            "project_input_authorization_invalid",
            "Project input authorization snapshot is missing",
            {},
        )
    return snapshot


def _uuid_set(value: object) -> set[UUID]:
    if not isinstance(value, list):
        raise ProjectBoundaryViolationError(
            "project_input_scope_denied",
            "Project input file scope is invalid",
            {},
        )
    try:
        ids = [UUID(str(item)) for item in value]
    except (TypeError, ValueError) as exc:
        raise ProjectBoundaryViolationError(
            "project_input_scope_denied",
            "Project input file scope is invalid",
            {},
        ) from exc
    if len(ids) != len(set(ids)):
        raise ProjectBoundaryViolationError(
            "project_input_scope_denied",
            "Project input file scope contains duplicate identifiers",
            {},
        )
    return set(ids)
