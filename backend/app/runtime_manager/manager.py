from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.cleanup import (
    RuntimeResourceCleaner,
    cleanup_succeeded,
)
from backend.app.runtime_manager.command_executor import RuntimeCommandExecutor
from backend.app.runtime_manager.command_output import lease_metadata
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.leases import RuntimeLeaseStore, RuntimeSpaceReservationStore
from backend.app.runtime_manager.metadata import (
    default_runtime_hardening_policy,
    runtime_hardening_metadata,
    runtime_isolation_metadata,
    runtime_labels,
    runtime_space_reservation_key,
    runtime_space_usage_for_runtime,
)
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_manager.runtime_guards import require_container
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtime_spaces.reservation_capacity import RuntimeSpaceCapacityReservationService
from backend.app.runtimes.models import (
    RuntimeCommand,
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
        self._events = RuntimeEventLog(session)
        self._leases = RuntimeLeaseStore(session)
        self._reservations = RuntimeSpaceReservationStore(session)
        self._commands = RuntimeCommandExecutor(
            session,
            docker_client,
            self._events,
            self._security_events,
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
            reservation_result = RuntimeSpaceCapacityReservationService(
                self._session
            ).reserve_run_capacity(
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
            self._reservations.release(runtime)
            self._session.delete(runtime)
            self._session.flush()
            raise
        runtime.docker_container_id = container_id
        runtime.status = "created"
        lease = self._leases.ensure(
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
        self._events.append(
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
        self._events.append(
            runtime,
            "runtime.lease_acquired",
            container_id,
            metadata={"runtime_lease_id": str(lease.id)},
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def start_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        require_container(runtime)
        self._docker.start_container(runtime.docker_container_id or "")
        runtime.status = "running"
        runtime.connection_status = "online"
        runtime.last_heartbeat_at = datetime.now(UTC)
        lease = self._leases.set_status(runtime, "running")
        self._events.append(runtime, "runtime.started", "", metadata=lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def stop_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        require_container(runtime)
        self._docker.stop_container(runtime.docker_container_id or "")
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        lease = self._leases.set_status(runtime, "stopped")
        self._events.append(runtime, "runtime.stopped", "", metadata=lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def delete_runtime(self, runtime: WorkspaceRuntime) -> None:
        require_container(runtime)
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
            self._events.append(
                runtime,
                "runtime.cleanup_failed",
                str(exc),
                metadata=cleanup,
            )
            self._security_events.record_cleanup_failure(runtime, cleanup, reason=str(exc))
            self._leases.set_status(runtime, "cleanup_failed", released_at=datetime.now(UTC))
            self._reservations.release(runtime)
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
        self._events.append(
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
        self._leases.set_status(
            runtime,
            "released" if runtime.status == "deleted" else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        self._reservations.release(runtime)
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
        self._events.append(
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
        self._leases.set_status(
            runtime,
            "released" if runtime.status == "deleted" else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        self._reservations.release(runtime)
        self._session.commit()

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        return self._commands.execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
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
        return self._commands.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )
