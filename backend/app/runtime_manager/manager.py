from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.command_executor import RuntimeCommandExecutor
from backend.app.runtime_manager.command_output import lease_metadata
from backend.app.runtime_manager.core.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeExecutionMode,
    RuntimeLimits,
    validate_runtime_execution_mode,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.lifecycle.cleanup import (
    RuntimeResourceCleaner,
    cleanup_succeeded,
)
from backend.app.runtime_manager.lifecycle.guards import require_container
from backend.app.runtime_manager.pool.leases import RuntimeLeaseStore, RuntimeSpaceReservationStore
from backend.app.runtime_manager.provisioning_executor import RuntimeProvisioningExecutor
from backend.app.runtime_manager.quotas import RuntimeQuotaPolicy
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtimes.models import (
    RuntimeCommand,
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
        execution_mode: RuntimeExecutionMode = "pooled",
        pool_key: str | None = None,
    ) -> WorkspaceRuntime:
        validate_runtime_execution_mode(execution_mode, pool_key)
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(workspace_id, limits)
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_template_id=template.id,
            runtime_space_id=runtime_space_id,
            name=name,
            execution_mode=execution_mode,
            pool_key=pool_key,
            limits={
                "cpu_count": limits.cpu_count,
                "memory_mb": limits.memory_mb,
                "disk_mb": limits.disk_mb,
                "timeout_seconds": limits.timeout_seconds,
                "max_output_bytes": limits.max_output_bytes,
                "max_processes": limits.max_processes,
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
        pool_key: str | None = None,
    ) -> WorkspaceRuntime:
        if execution_mode is not None:
            validate_runtime_execution_mode(execution_mode, pool_key)
            runtime.execution_mode = execution_mode
            runtime.pool_key = pool_key
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
        )

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

    def delete_runtime(
        self,
        runtime: WorkspaceRuntime,
        *,
        allow_active_pool_lease: bool = False,
    ) -> None:
        require_container(runtime)
        if not allow_active_pool_lease:
            self._require_pool_control_available(runtime)
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
        self._require_pool_control_available(runtime)
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
        self._require_pool_control_available(runtime)
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
        self._require_pool_control_available(runtime)
        return self._commands.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    def _require_pool_control_available(self, runtime: WorkspaceRuntime) -> None:
        if runtime.execution_pool_member_id is not None:
            return
        if runtime.execution_mode not in {"pooled", "persistent"}:
            return
        lease = self._session.scalar(
            select(RuntimeLease).where(
                RuntimeLease.workspace_id == runtime.workspace_id,
                RuntimeLease.workspace_runtime_id == runtime.id,
                RuntimeLease.status == "leased",
            )
        )
        if lease is not None:
            raise ValueError("Runtime is leased by an active run")


def _network_policy_from_metadata(
    *,
    network_disabled: bool,
    policy_metadata: dict[str, object] | None,
) -> dict[str, object]:
    effective = policy_metadata.get("effective") if isinstance(policy_metadata, dict) else None
    egress = effective.get("egress") if isinstance(effective, dict) else None
    if isinstance(egress, dict):
        return {"disabled": network_disabled, **egress}
    return {
        "disabled": network_disabled,
        "mode": "none" if network_disabled else "internet",
        "enforcement": "docker_network_none" if network_disabled else "docker_bridge",
    }
