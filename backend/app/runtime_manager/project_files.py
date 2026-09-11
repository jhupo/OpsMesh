from __future__ import annotations

from uuid import UUID

from backend.app.files.security import validate_runtime_relative_path
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.lifecycle.guards import require_container
from backend.app.runtimes.models import WorkspaceRuntime

RUNTIME_WORKSPACE_ROOT = "/workspace"
PROJECT_FILE_TRANSFER_TIMEOUT_SECONDS = 60


class DockerRunProjectFilesystem:
    def __init__(
        self,
        client: DockerRuntimeClient,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> None:
        require_container(runtime)
        self._client = client
        self._container_id = runtime.docker_container_id or ""
        self._root_path = f"{RUNTIME_WORKSPACE_ROOT}/runs/{run_id}"
        self._require_workspace_mount(runtime)

    @property
    def root_path(self) -> str:
        return self._root_path

    def stage_archive(self, archive: bytes) -> None:
        self._client.copy_archive_to_container(
            self._container_id,
            RUNTIME_WORKSPACE_ROOT,
            archive,
            PROJECT_FILE_TRANSFER_TIMEOUT_SECONDS,
        )

    def read_file(self, relative_path: str, *, max_bytes: int) -> bytes | None:
        normalized = validate_runtime_relative_path(relative_path)
        return self._client.copy_file_from_container(
            self._container_id,
            f"{self._root_path}/{normalized}",
            max_bytes,
            PROJECT_FILE_TRANSFER_TIMEOUT_SECONDS,
        )

    def cleanup(self) -> None:
        """Remove only this run's workspace tree inside the managed container."""
        result = self._client.exec_command(
            self._container_id,
            ["rm", "-rf", "--", self._root_path],
            PROJECT_FILE_TRANSFER_TIMEOUT_SECONDS,
            working_dir="/",
        )
        if result.exit_code != 0:
            raise RuntimeError("Runtime run workspace cleanup failed")

    @staticmethod
    def _require_workspace_mount(runtime: WorkspaceRuntime) -> None:
        isolation = runtime.capabilities.get("isolation")
        workspace_mount = isolation.get("workspace_mount") if isinstance(isolation, dict) else None
        if (
            not isinstance(workspace_mount, dict)
            or workspace_mount.get("target") != RUNTIME_WORKSPACE_ROOT
            or workspace_mount.get("mode") != "rw"
        ):
            raise ValueError("Docker runtime workspace mount is unavailable for project files")
