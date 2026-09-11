from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from shutil import rmtree
from typing import NotRequired, TypedDict

from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtimes.models import WorkspaceRuntime


class CleanupDetails(TypedDict):
    action: str
    container_id: str | None
    success: bool
    checked_at: str
    container_removed: NotRequired[bool]
    host_resources: NotRequired[list[dict[str, object]]]
    error: NotRequired[str]


class CleanupEvidence(TypedDict):
    cleanup: CleanupDetails


class RuntimeResourceCleaner:
    def __init__(
        self,
        docker_client: DockerRuntimeClient,
        managed_host_roots: Iterable[Path | str] | None = None,
    ) -> None:
        self._docker = docker_client
        self._managed_host_roots = tuple(
            path.resolve() for path in (Path(root) for root in managed_host_roots or ())
        )

    def cleanup_runtime_resources(
        self,
        runtime: WorkspaceRuntime,
        *,
        action: str,
        container_id: str | None,
        container_removed: bool,
        error: str | None = None,
    ) -> dict[str, object]:
        host_resource_results = self._cleanup_host_resources(runtime)
        success = container_removed and all(
            result.get("success") is not False for result in host_resource_results
        )
        evidence = cleanup_evidence(
            action=action,
            container_id=container_id,
            success=success,
            error=error,
        )
        evidence["cleanup"]["container_removed"] = container_removed
        evidence["cleanup"]["host_resources"] = host_resource_results
        return dict(evidence)

    def _cleanup_host_resources(self, runtime: WorkspaceRuntime) -> list[dict[str, object]]:
        managed_resources = runtime.capabilities.get("managed_resources")
        if not isinstance(managed_resources, dict):
            return []
        results: list[dict[str, object]] = []
        for path_value in _string_items(managed_resources.get("temp_dirs")):
            results.append(self._cleanup_host_path(path_value, resource_type="temp_dir"))
        for path_value in _string_items(managed_resources.get("staged_files")):
            results.append(self._cleanup_host_path(path_value, resource_type="staged_file"))
        for volume in _string_items(managed_resources.get("docker_volumes")):
            results.append(self._cleanup_docker_volume(volume))
        return results

    def _cleanup_docker_volume(self, volume_name: str) -> dict[str, object]:
        try:
            self._docker.remove_volume(volume_name)
        except Exception as exc:
            return resource_result(
                resource_type="docker_volume",
                target=volume_name,
                status="delete_failed",
                success=False,
                message=str(exc),
            )
        return resource_result(
            resource_type="docker_volume",
            target=volume_name,
            status="deleted",
            success=True,
        )

    def _cleanup_host_path(self, raw_path: str, *, resource_type: str) -> dict[str, object]:
        try:
            path = Path(raw_path).resolve()
        except OSError as exc:
            return resource_result(
                resource_type=resource_type,
                target=raw_path,
                status="invalid_path",
                success=False,
                message=str(exc),
            )
        if not self._is_managed_host_path(path):
            return resource_result(
                resource_type=resource_type,
                target=str(path),
                status="unsafe_path",
                success=False,
                message="Path is outside configured managed runtime roots.",
            )
        if not path.exists():
            return resource_result(
                resource_type=resource_type,
                target=str(path),
                status="already_absent",
                success=True,
            )
        try:
            if path.is_dir():
                rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            return resource_result(
                resource_type=resource_type,
                target=str(path),
                status="delete_failed",
                success=False,
                message=str(exc),
            )
        return resource_result(
            resource_type=resource_type,
            target=str(path),
            status="deleted",
            success=not path.exists(),
        )

    def _is_managed_host_path(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self._managed_host_roots)


def cleanup_evidence(
    *,
    action: str,
    container_id: str | None,
    success: bool,
    error: str | None = None,
) -> CleanupEvidence:
    evidence: CleanupEvidence = {
        "cleanup": {
            "action": action,
            "container_id": container_id,
            "success": success,
            "checked_at": datetime.now(UTC).isoformat(),
        }
    }
    if error is not None:
        evidence["cleanup"]["error"] = error
    return evidence


def cleanup_succeeded(evidence: dict[str, object]) -> bool:
    cleanup = evidence.get("cleanup")
    return isinstance(cleanup, dict) and cleanup.get("success") is True


def resource_result(
    *,
    resource_type: str,
    target: str,
    status: str,
    success: bool,
    message: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": resource_type,
        "target": target,
        "status": status,
        "success": success,
    }
    if message is not None:
        result["message"] = message
    return result


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
