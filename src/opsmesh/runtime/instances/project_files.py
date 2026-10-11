from __future__ import annotations

import io
import tarfile
from uuid import UUID

from opsmesh.resources.files.security import validate_runtime_relative_path
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.instances.policies.runtime import require_container

RUNTIME_WORKSPACE_ROOT = "/workspace"


class DockerRunProjectFilesystem:
    def __init__(
        self,
        client: DockerRuntimeClient,
        runtime: WorkspaceRuntime,
        run_id: UUID,
        *,
        timeout_seconds: int,
    ) -> None:
        require_container(runtime)
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._container_id = runtime.docker_container_id or ""
        from sqlalchemy.orm import object_session

        from opsmesh.runtime.instances.allocations import (
            RuntimeAllocationStore,
            allocation_identity,
        )

        session = object_session(runtime)
        if session is None:
            raise RuntimeError("Runtime filesystem requires a persisted identity")
        allocation = RuntimeAllocationStore(session).get(runtime, "run", run_id)
        if allocation is None:
            raise RuntimeError("Run filesystem has no execution allocation")
        self._identity = allocation_identity(allocation)
        self._root_path = f"{RUNTIME_WORKSPACE_ROOT}/runs/{run_id}"
        self._archive_prefix = f"runs/{run_id}"
        self._require_workspace_mount(runtime)

    @property
    def root_path(self) -> str:
        return self._root_path

    def stage_archive(self, archive: bytes) -> None:
        self._client.copy_archive_to_container(
            self._container_id,
            self._root_path,
            self._scoped_archive(archive),
            self._timeout_seconds,
        )
        result = self._client.exec_command(
            self._container_id,
            ["chown", "-R", f"{self._identity.uid}:{self._identity.uid}", self._root_path],
            self._timeout_seconds,
            working_dir="/",
        )
        if result.exit_code:
            raise RuntimeError("Runtime filesystem identity could not be assigned")
        sealed = self._client.exec_command(
            self._container_id,
            ["chmod", "700", self._root_path],
            self._timeout_seconds,
            working_dir="/",
            identity=self._identity,
        )
        if sealed.exit_code:
            raise RuntimeError("Runtime filesystem could not be sealed")

    def _scoped_archive(self, archive: bytes) -> bytes:
        output = io.BytesIO()
        with (
            tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source,
            tarfile.open(fileobj=output, mode="w") as target,
        ):
            for member in source.getmembers():
                if member.isdir() and member.name in {"runs", self._archive_prefix}:
                    continue
                if not member.name.startswith(f"{self._archive_prefix}/"):
                    raise ValueError("Project archive is outside its run workspace")
                if not (member.isfile() or member.isdir()):
                    raise ValueError("Project archive contains an unsupported entry")
                stream = source.extractfile(member) if member.isfile() else None
                member.name = validate_runtime_relative_path(
                    member.name.removeprefix(f"{self._archive_prefix}/")
                )
                target.addfile(member, stream)
        return output.getvalue()

    def read_file(self, relative_path: str, *, max_bytes: int) -> bytes | None:
        normalized = validate_runtime_relative_path(relative_path)
        return self._client.copy_file_from_container(
            self._container_id,
            f"{self._root_path}/{normalized}",
            max_bytes,
            self._timeout_seconds,
            identity=self._identity,
        )

    def cleanup(self) -> None:
        """Remove only this run's workspace tree inside the managed container."""
        result = self._client.exec_command(
            self._container_id,
            ["rm", "-rf", "--", self._root_path],
            self._timeout_seconds,
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
