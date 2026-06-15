from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from subprocess import TimeoutExpired
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.cleanup import (
    RuntimeResourceCleaner,
    cleanup_succeeded,
)
from backend.app.runtime_manager.command_output import (
    bounded_error,
    bounded_text,
    command_failure_metadata,
    lease_metadata,
    positive_int_limit,
)
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.metadata import (
    default_runtime_hardening_policy,
    runtime_hardening_metadata,
    runtime_isolation_metadata,
    runtime_labels,
    runtime_space_reservation_key,
    runtime_space_usage_for_runtime,
)
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtime_spaces.models import RuntimeSpaceEvent
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeLease,
    RuntimeTemplate,
    WorkspaceRuntime,
)


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
        self._cleaner = RuntimeResourceCleaner(docker_client, managed_host_roots)
        self._security_events = RuntimeSecurityEventRecorder(session)

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
        return self.provision_runtime(
            runtime,
            template=template,
            limits=limits,
            network_disabled=network_disabled,
            policy_metadata=policy_metadata,
        )

    def provision_runtime(
        self,
        runtime: WorkspaceRuntime,
        *,
        template: RuntimeTemplate,
        limits: RuntimeLimits,
        network_disabled: bool,
        policy_metadata: dict[str, object] | None = None,
    ) -> WorkspaceRuntime:
        workspace_id = runtime.workspace_id
        runtime_space_id = runtime.runtime_space_id
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(workspace_id, limits)
        isolation_metadata = runtime_isolation_metadata(
            workspace_id=workspace_id,
            runtime_id=runtime.id,
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
        )
        hardening_policy = default_runtime_hardening_policy()
        hardening_metadata = runtime_hardening_metadata(
            hardening_policy,
            isolation_metadata=isolation_metadata,
        )
        runtime.capabilities = {
            **dict(runtime.capabilities or {}),
            "isolation": isolation_metadata,
            "hardening": hardening_metadata,
            "policy_resolution": dict(policy_metadata or {}),
            "managed_resources": {
                "docker_volumes": [isolation_metadata["workspace_mount"]["docker_volume"]],
            },
        }
        reservation_key = runtime_space_reservation_key(runtime)
        if runtime_space_id is not None:
            reservation_result = RuntimeSpaceService(self._session).reserve_run_capacity(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                task_id=None,
                task_step_id=None,
                reservation_key=reservation_key,
                resource_usage=runtime_space_usage_for_runtime(limits),
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
                    name=f"opsmesh-{workspace_id}-{runtime.id}",
                    workspace_id=str(workspace_id),
                    runtime_id=str(runtime.id),
                    runtime_space_id=str(runtime_space_id) if runtime_space_id else None,
                    limits=limits,
                    network_disabled=network_disabled,
                    labels=runtime_labels(runtime),
                    mounts=(
                        RuntimeMount(
                            source=isolation_metadata["workspace_mount"]["docker_volume"],
                            target=isolation_metadata["workspace_mount"]["target"],
                        ),
                    ),
                    hardening=hardening_policy,
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
                "hardening": hardening_metadata,
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
                "hardening": hardening_metadata,
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
        self._append_event(runtime, "runtime.started", "", metadata=lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def stop_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        self._require_container(runtime)
        self._docker.stop_container(runtime.docker_container_id or "")
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        lease = self._set_runtime_lease_status(runtime, "stopped")
        self._append_event(runtime, "runtime.stopped", "", metadata=lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def delete_runtime(self, runtime: WorkspaceRuntime) -> None:
        self._require_container(runtime)
        container_id = runtime.docker_container_id or ""
        try:
            self._docker.remove_container(container_id)
        except Exception as exc:
            cleanup = self._cleaner.cleanup_runtime_resources(
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
            self._security_events.record_cleanup_failure(runtime, cleanup, reason=str(exc))
            self._set_runtime_lease_status(runtime, "cleanup_failed", released_at=datetime.now(UTC))
            self._release_runtime_space_reservation(runtime)
            self._session.commit()
            raise
        cleanup = self._cleaner.cleanup_runtime_resources(
            runtime,
            action="delete",
            container_id=container_id,
            container_removed=True,
        )
        runtime.status = "deleted" if cleanup_succeeded(cleanup) else "cleanup_failed"
        runtime.connection_status = "offline"
        self._append_event(
            runtime,
            "runtime.deleted" if runtime.status == "deleted" else "runtime.cleanup_failed",
            "" if runtime.status == "deleted" else "Managed runtime resource cleanup failed",
            metadata=cleanup,
        )
        if runtime.status == "cleanup_failed":
            self._security_events.record_cleanup_failure(
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
        cleanup = self._cleaner.cleanup_runtime_resources(
            runtime,
            action="stale_cleanup",
            container_id=container_id,
            container_removed=container_removed,
            error=error,
        )
        runtime.status = "deleted" if cleanup_succeeded(cleanup) else "cleanup_failed"
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
            self._security_events.record_cleanup_failure(
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
        return self.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )

    def execute_existing_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
    ) -> RuntimeCommand:
        if runtime.workspace_id != workspace_id:
            raise PermissionError("Runtime does not belong to workspace")
        self._require_container(runtime)
        timeout_value = runtime.limits.get("timeout_seconds", 60)
        timeout_seconds = timeout_value if isinstance(timeout_value, int) else 60
        record.command = command
        record.status = "running"
        record.started_at = datetime.now(UTC)
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
                metadata=command_failure_metadata(record, "timeout", str(exc)),
            )
        except Exception as exc:
            self._fail_command(
                record,
                status="failed",
                exit_code=None,
                stderr=bounded_error(exc),
            )
            self._append_event(
                runtime,
                "runtime.command.failed",
                " ".join(command),
                metadata=command_failure_metadata(record, "docker_exec_failed", str(exc)),
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
        max_output_bytes = positive_int_limit(runtime.limits.get("max_output_bytes"), 256_000)
        stdout, stdout_truncated, stdout_bytes = bounded_text(result.stdout, max_output_bytes)
        stderr, stderr_truncated, stderr_bytes = bounded_text(result.stderr, max_output_bytes)
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
            self._security_events.record_policy_limit(
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
            reservation_key=runtime_space_reservation_key(runtime),
            released_at=datetime.now(UTC),
        )
