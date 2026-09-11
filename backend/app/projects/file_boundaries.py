from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.files.models import WorkspaceFile
from backend.app.files.runtime_policy import runtime_file_denial_code
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.orchestration.run_request.authorization import RunAuthorizationService
from backend.app.orchestration.run_runtime_authorization import (
    RunRuntimeAuthorizationError,
    runtime_binding_for_snapshot,
)
from backend.app.projects.models import AgentRunProjectSnapshot
from backend.app.projects.run_manifest import RunProjectManifest
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.models import Task

MAX_PROJECT_INPUT_FILES = 512
MAX_PROJECT_OUTPUTS = 128
MAX_PROJECT_INPUT_BYTES = 536_870_912
MAX_PROJECT_OUTPUT_BYTES = 1_073_741_824
MAX_PROJECT_WORKSPACE_BYTES = MAX_PROJECT_INPUT_BYTES + MAX_PROJECT_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class ProjectBoundaryViolation(ValueError):
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
                None,
                snapshot,
                lock_resources=True,
            )
            self._validate_current_files(run, manifest)
            self._validate_runtime_capacity(runtime, manifest)
        except ProjectBoundaryViolation:
            raise
        except RunRuntimeAuthorizationError as exc:
            raise ProjectBoundaryViolation(
                code="project_input_authorization_denied",
                message="Project inputs are not authorized for this runtime",
                metadata={"authorization_reason": exc.code},
            ) from exc
        except ValueError as exc:
            raise ProjectBoundaryViolation(
                code="project_input_authorization_invalid",
                message="Project input authorization failed integrity validation",
                metadata={},
            ) from exc

    @staticmethod
    def as_io_error(
        violation: ProjectBoundaryViolation,
        *,
        stage: str,
    ) -> ProjectRunIOError:
        return ProjectRunIOError(
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
            raise ProjectBoundaryViolation(
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
            raise ProjectBoundaryViolation(
                "project_input_scope_denied",
                "Project input scope does not match the run task",
                {},
            )
        snapshot = _authorization_snapshot(run)
        if snapshot.get("version") != 2:
            raise ProjectBoundaryViolation(
                "project_input_authorization_invalid",
                "Project input authorization snapshot is invalid",
                {},
            )
        fingerprint = snapshot.get("fingerprint")
        if not isinstance(fingerprint, str) or fingerprint != authorization_snapshot_fingerprint(
            snapshot
        ):
            raise ProjectBoundaryViolation(
                "project_input_authorization_invalid",
                "Project input authorization snapshot failed integrity validation",
                {},
            )
        if snapshot.get("workspace_id") != str(run.workspace_id) or snapshot.get(
            "task_id"
        ) != str(task.id):
            raise ProjectBoundaryViolation(
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
            raise ProjectBoundaryViolation(
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
            raise ProjectBoundaryViolation(
                "project_input_scope_denied",
                "Project input runtime binding is invalid",
                {},
            )
        manifest_file_ids = {item.workspace_file_id for item in manifest.files}
        missing_ids = manifest_file_ids - allowed_file_ids
        if missing_ids:
            raise ProjectBoundaryViolation(
                "project_input_grant_denied",
                "One or more project inputs are outside the authorized file scope",
                {"denied_file_count": len(missing_ids)},
            )

    @staticmethod
    def _validate_manifest_capacity(manifest: RunProjectManifest) -> None:
        input_bytes = sum(item.size_bytes for item in manifest.files)
        output_bytes = sum(item.max_bytes for item in manifest.outputs)
        if len(manifest.files) > MAX_PROJECT_INPUT_FILES:
            raise ProjectBoundaryViolation(
                "project_input_file_limit_exceeded",
                "Project input count exceeds the runtime staging limit",
                {"file_count": len(manifest.files), "limit": MAX_PROJECT_INPUT_FILES},
            )
        if input_bytes > MAX_PROJECT_INPUT_BYTES:
            raise ProjectBoundaryViolation(
                "project_input_byte_limit_exceeded",
                "Project inputs exceed the runtime staging byte limit",
                {"input_bytes": input_bytes, "limit": MAX_PROJECT_INPUT_BYTES},
            )
        if len(manifest.outputs) > MAX_PROJECT_OUTPUTS:
            raise ProjectBoundaryViolation(
                "project_output_count_limit_exceeded",
                "Project output count exceeds the runtime collection limit",
                {"output_count": len(manifest.outputs), "limit": MAX_PROJECT_OUTPUTS},
            )
        if output_bytes > MAX_PROJECT_OUTPUT_BYTES:
            raise ProjectBoundaryViolation(
                "project_output_byte_limit_exceeded",
                "Declared project outputs exceed the runtime collection byte limit",
                {"output_bytes": output_bytes, "limit": MAX_PROJECT_OUTPUT_BYTES},
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
                raise ProjectBoundaryViolation(
                    "project_input_unavailable",
                    "An authorized project input is unavailable",
                    {"project_file_id": str(item.project_file_id)},
                )
            if (
                file.size_bytes != item.size_bytes
                or file.checksum_sha256 != item.checksum_sha256
                or file.storage_key != item.storage_key
            ):
                raise ProjectBoundaryViolation(
                    "project_input_snapshot_mismatch",
                    "An authorized project input no longer matches its snapshot",
                    {"project_file_id": str(item.project_file_id)},
                )
            denial_code = runtime_file_denial_code(file)
            if denial_code is not None:
                raise ProjectBoundaryViolation(
                    denial_code,
                    "A project input is blocked by the workspace file runtime policy",
                    {"project_file_id": str(item.project_file_id)},
                )

    @staticmethod
    def _validate_runtime_capacity(
        runtime: WorkspaceRuntime,
        manifest: RunProjectManifest,
    ) -> None:
        required_bytes = sum(item.size_bytes for item in manifest.files) + sum(
            item.max_bytes for item in manifest.outputs
        )
        capacity_bytes = MAX_PROJECT_WORKSPACE_BYTES
        if runtime.runtime_provider in {"docker", "cloud_docker"}:
            disk_mb = runtime.limits.get("disk_mb")
            if isinstance(disk_mb, bool) or not isinstance(disk_mb, int) or disk_mb <= 0:
                raise ProjectBoundaryViolation(
                    "project_runtime_capacity_invalid",
                    "Managed project runtime has no valid disk capacity",
                    {},
                )
            capacity_bytes = disk_mb * 1_048_576
        elif runtime.runtime_provider == "self_hosted":
            declared = runtime.capabilities.get("max_project_bytes")
            if declared is not None:
                if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
                    raise ProjectBoundaryViolation(
                        "project_runtime_capacity_invalid",
                        "Self-hosted project runtime reported an invalid capacity",
                        {},
                    )
                capacity_bytes = min(capacity_bytes, declared)
        if required_bytes > capacity_bytes:
            raise ProjectBoundaryViolation(
                "project_runtime_capacity_exceeded",
                "Project inputs and declared outputs exceed runtime capacity",
                {"required_bytes": required_bytes, "capacity_bytes": capacity_bytes},
            )


def _authorization_snapshot(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    if not isinstance(snapshot, dict):
        raise ProjectBoundaryViolation(
            "project_input_authorization_invalid",
            "Project input authorization snapshot is missing",
            {},
        )
    return snapshot


def _uuid_set(value: object) -> set[UUID]:
    if not isinstance(value, list):
        raise ProjectBoundaryViolation(
            "project_input_scope_denied",
            "Project input file scope is invalid",
            {},
        )
    try:
        ids = [UUID(str(item)) for item in value]
    except (TypeError, ValueError) as exc:
        raise ProjectBoundaryViolation(
            "project_input_scope_denied",
            "Project input file scope is invalid",
            {},
        ) from exc
    if len(ids) != len(set(ids)):
        raise ProjectBoundaryViolation(
            "project_input_scope_denied",
            "Project input file scope contains duplicate identifiers",
            {},
        )
    return set(ids)
