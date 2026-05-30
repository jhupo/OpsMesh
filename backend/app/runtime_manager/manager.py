from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from shutil import rmtree
from subprocess import TimeoutExpired
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_spaces.models import RuntimeSpaceEvent
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeLease,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.security.models import SecurityEvent


class RuntimeManager:
    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient,
        *,
        managed_host_roots: Iterable[Path | str] | None = None,
    ) -> None:
        self._session = session
        self._docker = docker_client
        self._managed_host_roots = tuple(
            path.resolve() for path in (Path(root) for root in managed_host_roots or ())
        )

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template: RuntimeTemplate,
        name: str,
        limits: RuntimeLimits,
        runtime_space_id: UUID | None = None,
        network_disabled: bool = True,
        policy_metadata: dict[str, object] | None = None,
    ) -> WorkspaceRuntime:
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(workspace_id, limits)
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_template_id=template.id,
            runtime_space_id=runtime_space_id,
            name=name,
            limits={
                "cpu_count": limits.cpu_count,
                "memory_mb": limits.memory_mb,
                "disk_mb": limits.disk_mb,
                "timeout_seconds": limits.timeout_seconds,
                "max_output_bytes": limits.max_output_bytes,
                "max_processes": limits.max_processes,
            },
            network_policy={"disabled": network_disabled},
            capabilities={},
        )
        self._session.add(runtime)
        self._session.flush()
        isolation_metadata = _runtime_isolation_metadata(
            workspace_id=workspace_id,
            runtime_id=runtime.id,
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
        )
        runtime.capabilities = {
            "isolation": isolation_metadata,
            "policy_resolution": dict(policy_metadata or {}),
            "managed_resources": {
                "docker_volumes": [isolation_metadata["workspace_mount"]["docker_volume"]],
            },
        }
        reservation_key = _runtime_space_reservation_key(runtime)
        if runtime_space_id is not None:
            reservation_result = RuntimeSpaceService(self._session).reserve_run_capacity(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                task_id=None,
                task_step_id=None,
                reservation_key=reservation_key,
                resource_usage=_runtime_space_usage_for_runtime(limits),
            )
            if reservation_result.reservation is None:
                self._session.delete(runtime)
                self._session.flush()
                blocked_reason = reservation_result.blocked_reason or "runtime_space_quota_exceeded"
                raise RuntimeQuotaExceededError(
                    blocked_reason,
                    "Runtime space quota blocks Docker runtime creation",
                )

        try:
            container_id = self._docker.create_container(
                RuntimeCreateRequest(
                    image=template.image,
                    name=f"chaincloud-{workspace_id}-{runtime.id}",
                    workspace_id=str(workspace_id),
                    runtime_id=str(runtime.id),
                    runtime_space_id=str(runtime_space_id) if runtime_space_id else None,
                    limits=limits,
                    network_disabled=network_disabled,
                    labels=_runtime_labels(runtime),
                    mounts=(
                        RuntimeMount(
                            source=isolation_metadata["workspace_mount"]["docker_volume"],
                            target=isolation_metadata["workspace_mount"]["target"],
                        ),
                    ),
                    working_dir=isolation_metadata["workspace_mount"]["target"],
                )
            )
        except Exception:
            self._release_runtime_space_reservation(runtime)
            self._session.delete(runtime)
            self._session.flush()
            raise
        runtime.docker_container_id = container_id
        runtime.status = "created"
        lease = self._ensure_runtime_lease(
            runtime,
            status="active",
            metadata={
                "action": "create",
                "image": template.image,
                "limits": dict(runtime.limits),
                "network_policy": dict(runtime.network_policy),
                "isolation": isolation_metadata,
                "policy_resolution": dict(policy_metadata or {}),
                "runtime_space_reservation_key": reservation_key
                if runtime_space_id is not None
                else None,
            },
        )
        self._append_event(
            runtime,
            "runtime.created",
            container_id,
            metadata={
                "isolation": isolation_metadata,
                "network_policy": dict(runtime.network_policy),
                "policy_resolution": dict(policy_metadata or {}),
            },
        )
        self._append_event(
            runtime,
            "runtime.lease_acquired",
            container_id,
            metadata={"runtime_lease_id": str(lease.id)},
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def start_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        self._require_container(runtime)
        self._docker.start_container(runtime.docker_container_id or "")
        runtime.status = "running"
        runtime.connection_status = "online"
        runtime.last_heartbeat_at = datetime.now(UTC)
        lease = self._set_runtime_lease_status(runtime, "running")
        self._append_event(runtime, "runtime.started", "", metadata=_lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def stop_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        self._require_container(runtime)
        self._docker.stop_container(runtime.docker_container_id or "")
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        lease = self._set_runtime_lease_status(runtime, "stopped")
        self._append_event(runtime, "runtime.stopped", "", metadata=_lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def delete_runtime(self, runtime: WorkspaceRuntime) -> None:
        self._require_container(runtime)
        container_id = runtime.docker_container_id or ""
        try:
            self._docker.remove_container(container_id)
        except Exception as exc:
            cleanup = self._cleanup_runtime_resources(
                runtime,
                action="delete",
                container_id=container_id,
                container_removed=False,
                error=str(exc),
            )
            runtime.status = "cleanup_failed"
            runtime.connection_status = "offline"
            self._append_event(
                runtime,
                "runtime.cleanup_failed",
                str(exc),
                metadata=cleanup,
            )
            self._record_cleanup_security_event(runtime, cleanup, reason=str(exc))
            self._set_runtime_lease_status(runtime, "cleanup_failed", released_at=datetime.now(UTC))
            self._release_runtime_space_reservation(runtime)
            self._session.commit()
            raise
        cleanup = self._cleanup_runtime_resources(
            runtime,
            action="delete",
            container_id=container_id,
            container_removed=True,
        )
        runtime.status = "deleted" if _cleanup_succeeded(cleanup) else "cleanup_failed"
        runtime.connection_status = "offline"
        self._append_event(
            runtime,
            "runtime.deleted" if runtime.status == "deleted" else "runtime.cleanup_failed",
            "" if runtime.status == "deleted" else "Managed runtime resource cleanup failed",
            metadata=cleanup,
        )
        if runtime.status == "cleanup_failed":
            self._record_cleanup_security_event(
                runtime,
                cleanup,
                reason="Managed runtime resource cleanup failed",
            )
        self._set_runtime_lease_status(
            runtime,
            "released" if runtime.status == "deleted" else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        self._release_runtime_space_reservation(runtime)
        self._session.commit()

    def cleanup_stale_runtime(self, runtime: WorkspaceRuntime) -> None:
        if runtime.status not in {"stopped", "failed", "deleted"}:
            return
        container_id = runtime.docker_container_id
        container_removed = True
        error: str | None = None
        if container_id:
            try:
                self._docker.remove_container(container_id)
            except Exception as exc:
                container_removed = False
                error = str(exc)
        cleanup = self._cleanup_runtime_resources(
            runtime,
            action="stale_cleanup",
            container_id=container_id,
            container_removed=container_removed,
            error=error,
        )
        runtime.status = "deleted" if _cleanup_succeeded(cleanup) else "cleanup_failed"
        runtime.connection_status = "offline"
        self._append_event(
            runtime,
            "runtime.cleanup" if runtime.status == "deleted" else "runtime.cleanup_failed",
            ""
            if runtime.status == "deleted"
            else error or "Managed runtime resource cleanup failed",
            metadata=cleanup,
        )
        if runtime.status == "cleanup_failed":
            self._record_cleanup_security_event(
                runtime,
                cleanup,
                reason=error or "Managed runtime resource cleanup failed",
            )
        self._set_runtime_lease_status(
            runtime,
            "released" if runtime.status == "deleted" else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        self._release_runtime_space_reservation(runtime)
        self._session.commit()

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        if runtime.workspace_id != workspace_id:
            raise PermissionError("Runtime does not belong to workspace")
        self._require_container(runtime)
        timeout_value = runtime.limits.get("timeout_seconds", 60)
        timeout_seconds = timeout_value if isinstance(timeout_value, int) else 60
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="running",
            started_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()

        try:
            result = self._docker.exec_command(
                runtime.docker_container_id or "",
                command,
                timeout_seconds,
            )
        except TimeoutExpired as exc:
            self._fail_command(
                record,
                status="timeout",
                exit_code=None,
                stderr=f"Command exceeded timeout of {timeout_seconds} seconds",
            )
            self._append_event(
                runtime,
                "runtime.command.timeout",
                " ".join(command),
                metadata=_command_failure_metadata(record, "timeout", str(exc)),
            )
        except Exception as exc:
            self._fail_command(
                record,
                status="failed",
                exit_code=None,
                stderr=_bounded_error(exc),
            )
            self._append_event(
                runtime,
                "runtime.command.failed",
                " ".join(command),
                metadata=_command_failure_metadata(record, "docker_exec_failed", str(exc)),
            )
        else:
            self._complete_command(runtime, record, result)
            self._append_event(runtime, "runtime.command.completed", " ".join(command))
        self._session.commit()
        self._session.refresh(record)
        return record

    def _complete_command(
        self,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        result: RuntimeCommandResult,
    ) -> None:
        record.exit_code = result.exit_code
        max_output_bytes = _positive_int_limit(runtime.limits.get("max_output_bytes"), 256_000)
        stdout, stdout_truncated, stdout_bytes = _bounded_text(result.stdout, max_output_bytes)
        stderr, stderr_truncated, stderr_bytes = _bounded_text(result.stderr, max_output_bytes)
        record.stdout = stdout
        record.stderr = stderr
        record.status = "completed" if result.exit_code == 0 else "failed"
        record.completed_at = datetime.now(UTC)
        if stdout_truncated or stderr_truncated:
            metadata = {
                "command_id": str(record.id),
                "max_output_bytes": max_output_bytes,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
            self._append_event(
                runtime,
                "runtime.command.output_limited",
                "Command output exceeded runtime policy.",
                metadata=metadata,
            )
            self._record_policy_security_event(
                runtime,
                action="runtime.command.output_limited",
                reason="Runtime command output exceeded policy.",
                metadata=metadata,
            )

    def _fail_command(
        self,
        record: RuntimeCommand,
        *,
        status: str,
        exit_code: int | None,
        stderr: str,
    ) -> None:
        record.exit_code = exit_code
        record.stdout = ""
        record.stderr = stderr
        record.status = status
        record.completed_at = datetime.now(UTC)

    def _append_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        created_at = datetime.now(UTC)
        event_metadata = {"runtime_id": str(runtime.id)} | (metadata or {})
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata=event_metadata,
                created_at=created_at,
            )
        )
        if runtime.runtime_space_id is not None:
            self._session.add(
                RuntimeSpaceEvent(
                    workspace_id=runtime.workspace_id,
                    runtime_space_id=runtime.runtime_space_id,
                    event_type=event_type,
                    message=message,
                    event_metadata={
                        "runtime_id": str(runtime.id),
                        "runtime_status": runtime.status,
                        "connection_status": runtime.connection_status,
                        **(metadata or {}),
                    },
                    created_at=created_at,
                )
            )

    def _require_container(self, runtime: WorkspaceRuntime) -> None:
        if not runtime.docker_container_id:
            raise ValueError("Runtime has no Docker container")

    def _ensure_runtime_lease(
        self,
        runtime: WorkspaceRuntime,
        *,
        status: str,
        metadata: dict[str, object],
    ) -> RuntimeLease:
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        now = datetime.now(UTC)
        if lease is None:
            lease = RuntimeLease(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                docker_container_id=runtime.docker_container_id,
                status=status,
                lease_metadata=metadata,
                acquired_at=now,
            )
            self._session.add(lease)
            self._session.flush([lease])
            return lease
        lease.runtime_space_id = runtime.runtime_space_id
        lease.docker_container_id = runtime.docker_container_id
        lease.status = status
        lease.lease_metadata = lease.lease_metadata | metadata
        if status not in {"released", "cleanup_failed"}:
            lease.released_at = None
        self._session.flush([lease])
        return lease

    def _set_runtime_lease_status(
        self,
        runtime: WorkspaceRuntime,
        status: str,
        *,
        released_at: datetime | None = None,
    ) -> RuntimeLease | None:
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        if lease is None:
            return None
        lease.status = status
        lease.runtime_space_id = runtime.runtime_space_id
        lease.docker_container_id = runtime.docker_container_id
        if released_at is not None:
            lease.released_at = released_at
        self._session.flush([lease])
        return lease

    def _release_runtime_space_reservation(self, runtime: WorkspaceRuntime) -> bool:
        if runtime.runtime_space_id is None:
            return False
        return RuntimeSpaceService(self._session).release_reservation_by_key(
            workspace_id=runtime.workspace_id,
            runtime_space_id=runtime.runtime_space_id,
            reservation_key=_runtime_space_reservation_key(runtime),
            released_at=datetime.now(UTC),
        )

    def _cleanup_runtime_resources(
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
        evidence = _cleanup_evidence(
            action=action,
            container_id=container_id,
            success=success,
            error=error,
        )
        evidence["cleanup"]["container_removed"] = container_removed
        evidence["cleanup"]["host_resources"] = host_resource_results
        return evidence

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
            return _resource_result(
                resource_type="docker_volume",
                target=volume_name,
                status="delete_failed",
                success=False,
                message=str(exc),
            )
        return _resource_result(
            resource_type="docker_volume",
            target=volume_name,
            status="deleted",
            success=True,
        )

    def _cleanup_host_path(self, raw_path: str, *, resource_type: str) -> dict[str, object]:
        try:
            path = Path(raw_path).resolve()
        except OSError as exc:
            return _resource_result(
                resource_type=resource_type,
                target=raw_path,
                status="invalid_path",
                success=False,
                message=str(exc),
            )
        if not self._is_managed_host_path(path):
            return _resource_result(
                resource_type=resource_type,
                target=str(path),
                status="unsafe_path",
                success=False,
                message="Path is outside configured managed runtime roots.",
            )
        if not path.exists():
            return _resource_result(
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
            return _resource_result(
                resource_type=resource_type,
                target=str(path),
                status="delete_failed",
                success=False,
                message=str(exc),
            )
        return _resource_result(
            resource_type=resource_type,
            target=str(path),
            status="deleted",
            success=not path.exists(),
        )

    def _is_managed_host_path(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self._managed_host_roots)

    def _record_cleanup_security_event(
        self,
        runtime: WorkspaceRuntime,
        cleanup: dict[str, object],
        *,
        reason: str,
    ) -> None:
        self._session.add(
            SecurityEvent(
                workspace_id=runtime.workspace_id,
                user_id=None,
                action="runtime.cleanup.failed",
                outcome="failed",
                severity="critical",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="runtime_manager",
                method="SYSTEM",
                reason=reason[:512],
                event_metadata={
                    "runtime_id": str(runtime.id),
                    "runtime_space_id": str(runtime.runtime_space_id)
                    if runtime.runtime_space_id
                    else None,
                    "docker_container_id": runtime.docker_container_id,
                    **cleanup,
                },
                created_at=datetime.now(UTC),
            )
        )

    def _record_policy_security_event(
        self,
        runtime: WorkspaceRuntime,
        *,
        action: str,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            SecurityEvent(
                workspace_id=runtime.workspace_id,
                user_id=None,
                action=action,
                outcome="limited",
                severity="warning",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="runtime_manager",
                method="SYSTEM",
                reason=reason[:512],
                event_metadata={
                    "runtime_id": str(runtime.id),
                    "runtime_space_id": str(runtime.runtime_space_id)
                    if runtime.runtime_space_id
                    else None,
                    "docker_container_id": runtime.docker_container_id,
                    **metadata,
                },
                created_at=datetime.now(UTC),
            )
        )


def _cleanup_evidence(
    *,
    action: str,
    container_id: str | None,
    success: bool,
    error: str | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
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


def _cleanup_succeeded(evidence: dict[str, object]) -> bool:
    cleanup = evidence.get("cleanup")
    return isinstance(cleanup, dict) and cleanup.get("success") is True


def _command_failure_metadata(
    record: RuntimeCommand,
    reason: str,
    error: str,
) -> dict[str, object]:
    return {
        "command_id": str(record.id),
        "command_status": record.status,
        "reason": reason,
        "error": error[:512],
    }


def _bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:2_000]


def _lease_metadata(lease: RuntimeLease | None) -> dict[str, object]:
    return {"runtime_lease_id": str(lease.id)} if lease is not None else {}


def _runtime_space_reservation_key(runtime: WorkspaceRuntime) -> str:
    return f"workspace_runtime:{runtime.id}:docker"


def _runtime_space_usage_for_runtime(limits: RuntimeLimits) -> dict[str, int]:
    return {
        "docker_runtimes": 1,
        "cpu": _ceil_positive_int(limits.cpu_count),
        "memory_mb": limits.memory_mb,
        "storage_mb": limits.disk_mb,
    }


def _ceil_positive_int(value: float) -> int:
    integer_value = int(value)
    if value > integer_value:
        integer_value += 1
    return max(1, integer_value)


def _runtime_isolation_metadata(
    *,
    workspace_id: UUID,
    runtime_id: UUID,
    runtime_space_id: UUID | None,
    network_disabled: bool,
) -> dict[str, object]:
    volume_name = _runtime_volume_name(workspace_id, runtime_id)
    return {
        "workspace_id": str(workspace_id),
        "runtime_id": str(runtime_id),
        "runtime_space_id": str(runtime_space_id) if runtime_space_id else None,
        "workspace_mount": {
            "type": "volume",
            "docker_volume": volume_name,
            "target": "/workspace",
            "mode": "rw",
        },
        "network": {
            "disabled": network_disabled,
            "mode": "none" if network_disabled else "bridge",
        },
    }


def _runtime_volume_name(workspace_id: UUID, runtime_id: UUID) -> str:
    return f"chaincloud-ws-{workspace_id.hex}-runtime-{runtime_id.hex}"


def _runtime_labels(runtime: WorkspaceRuntime) -> dict[str, str]:
    labels = {
        "chaincloud.managed": "true",
        "chaincloud.runtime_type": runtime.runtime_type,
        "chaincloud.runtime_provider": runtime.runtime_provider,
    }
    if runtime.runtime_space_id is not None:
        labels["chaincloud.runtime_space_id"] = str(runtime.runtime_space_id)
    policy_resolution = runtime.capabilities.get("policy_resolution")
    if isinstance(policy_resolution, dict):
        team = policy_resolution.get("team")
        if isinstance(team, dict) and isinstance(team.get("id"), str):
            labels["chaincloud.team_id"] = team["id"]
        runtime_space = policy_resolution.get("runtime_space")
        if isinstance(runtime_space, dict) and isinstance(runtime_space.get("scope"), str):
            labels["chaincloud.runtime_space_scope"] = runtime_space["scope"]
    return labels


def _positive_int_limit(value: object, fallback: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return fallback


def _bounded_text(value: str, max_bytes: int) -> tuple[str, bool, int]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False, len(encoded)
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True, len(encoded)


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _resource_result(
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
