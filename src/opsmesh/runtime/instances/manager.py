from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.runtime.commands.executor import RuntimeCommandExecutor
from opsmesh.runtime.commands.output import lease_metadata
from opsmesh.runtime.contracts import RuntimeExecutionMode, validate_runtime_execution_mode
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.cleanup import RuntimeResourceCleaner, cleanup_succeeded
from opsmesh.runtime.instances.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeLimits,
    RuntimeProcess,
)
from opsmesh.runtime.instances.events import RuntimeEventLog
from opsmesh.runtime.instances.leases import RuntimeLeaseStore, RuntimeSpaceReservationStore
from opsmesh.runtime.instances.models import (
    RuntimeCommand,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from opsmesh.runtime.instances.policies.quotas import RuntimeQuotaPolicy
from opsmesh.runtime.instances.policies.runtime import require_container
from opsmesh.runtime.instances.provisioning_executor import RuntimeProvisioningExecutor
from opsmesh.runtime.instances.security_events import RuntimeSecurityEventRecorder
from opsmesh.shared.config import Settings


class DockerRuntimeManagerProvider:
    """Lazily provides one Docker-backed manager for a request or worker transaction."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        docker_client: DockerRuntimeClient | None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._docker_client = docker_client
        self._manager: RuntimeManager | None = None

    def require(self) -> RuntimeManager:
        if self._docker_client is None:
            raise RuntimeError("Runtime manager execution requires an injected Docker client")
        if self._manager is None:
            self._manager = RuntimeManager(
                self._session,
                self._docker_client,
                managed_host_roots=[Path(self._settings.storage_root).resolve() / "runtimes"],
            )
        return self._manager


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
        execution_mode: RuntimeExecutionMode = "shared",
    ) -> WorkspaceRuntime:
        validate_runtime_execution_mode(execution_mode)
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(workspace_id, limits)
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_template_id=template.id,
            runtime_space_id=runtime_space_id,
            name=name,
            execution_mode=execution_mode,
            limits={
                "cpu_count": limits.cpu_count,
                "memory_mb": limits.memory_mb,
                "disk_mb": limits.disk_mb,
                "timeout_seconds": limits.timeout_seconds,
                "max_output_bytes": limits.max_output_bytes,
                "max_processes": limits.max_processes,
                "max_concurrent_executions": limits.max_concurrent_executions,
            },
            network_policy=_network_policy_from_metadata(
                network_disabled=network_disabled,
                policy_metadata=policy_metadata,
            ),
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
        execution_mode: RuntimeExecutionMode | None = None,
        process: RuntimeProcess | None = None,
    ) -> WorkspaceRuntime:
        if execution_mode is not None:
            validate_runtime_execution_mode(execution_mode)
            runtime.execution_mode = execution_mode
        return RuntimeProvisioningExecutor(
            self._session,
            self._docker,
            self._events,
            self._leases,
            self._reservations,
        ).provision_runtime(
            runtime,
            template=template,
            limits=limits,
            network_disabled=network_disabled,
            policy_metadata=policy_metadata,
            process=process,
        )

    def start_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        require_container(runtime)
        self._require_host_idle(runtime)
        runtime.status = "starting"
        runtime.connection_status = "offline"
        self._session.commit()
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
        self._require_host_idle(runtime)
        runtime.status = "stopping"
        runtime.connection_status = "offline"
        self._session.commit()
        self._docker.stop_container(runtime.docker_container_id or "")
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        lease = self._leases.set_status(runtime, "stopped")
        self._events.append(runtime, "runtime.stopped", "", metadata=lease_metadata(lease))
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def delete_runtime(
        self,
        runtime: WorkspaceRuntime,
    ) -> None:
        require_container(runtime)
        self._require_host_idle(runtime)
        runtime.status = "deleting"
        runtime.connection_status = "offline"
        self._session.commit()
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
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        self._require_host_idle(runtime)
        return self._commands.execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    async def execute_command_async(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        self._require_host_idle(runtime)
        return await self._commands.execute_command_async(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    def execute_existing_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        self._require_host_idle(runtime)
        return self._commands.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    def _require_host_idle(self, runtime: WorkspaceRuntime) -> None:
        if runtime.capabilities.get("node_id") != self._docker.node_identity():
            raise ValueError("Runtime belongs to another execution node")
        RuntimeAllocationStore(self._session).require_idle(runtime)


def _network_policy_from_metadata(
    *,
    network_disabled: bool,
    policy_metadata: dict[str, object] | None,
) -> dict[str, object]:
    effective = policy_metadata.get("effective") if isinstance(policy_metadata, dict) else None
    egress = effective.get("egress") if isinstance(effective, dict) else None
    if isinstance(egress, dict):
        return dict(egress)
    return {"mode": "none" if network_disabled else "internet"}
