from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
)
from backend.app.runtime_manager.quotas import RuntimeQuotaPolicy
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)


class RuntimeManager:
    def __init__(self, session: Session, docker_client: DockerRuntimeClient) -> None:
        self._session = session
        self._docker = docker_client

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template: RuntimeTemplate,
        name: str,
        limits: RuntimeLimits,
        runtime_space_id: UUID | None = None,
        network_disabled: bool = True,
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
            },
            network_policy={"disabled": network_disabled},
            capabilities={},
        )
        self._session.add(runtime)
        self._session.flush()

        container_id = self._docker.create_container(
            RuntimeCreateRequest(
                image=template.image,
                name=f"chaincloud-{workspace_id}-{runtime.id}",
                workspace_id=str(workspace_id),
                limits=limits,
                network_disabled=network_disabled,
            )
        )
        runtime.docker_container_id = container_id
        runtime.status = "created"
        self._append_event(runtime, "runtime.created", container_id)
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def start_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        self._require_container(runtime)
        self._docker.start_container(runtime.docker_container_id or "")
        runtime.status = "running"
        runtime.connection_status = "online"
        runtime.last_heartbeat_at = datetime.now(UTC)
        self._append_event(runtime, "runtime.started", "")
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def stop_runtime(self, runtime: WorkspaceRuntime) -> WorkspaceRuntime:
        self._require_container(runtime)
        self._docker.stop_container(runtime.docker_container_id or "")
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        self._append_event(runtime, "runtime.stopped", "")
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def delete_runtime(self, runtime: WorkspaceRuntime) -> None:
        self._require_container(runtime)
        self._docker.remove_container(runtime.docker_container_id or "")
        runtime.status = "deleted"
        runtime.connection_status = "offline"
        self._append_event(runtime, "runtime.deleted", "")
        self._session.commit()

    def cleanup_stale_runtime(self, runtime: WorkspaceRuntime) -> None:
        if runtime.status not in {"stopped", "failed", "deleted"}:
            return
        if runtime.docker_container_id:
            self._docker.remove_container(runtime.docker_container_id)
        runtime.status = "deleted"
        runtime.connection_status = "offline"
        self._append_event(runtime, "runtime.cleanup", "")
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

        result = self._docker.exec_command(
            runtime.docker_container_id or "",
            command,
            timeout_seconds,
        )
        self._complete_command(record, result)
        self._append_event(runtime, "runtime.command.completed", " ".join(command))
        self._session.commit()
        self._session.refresh(record)
        return record

    def _complete_command(self, record: RuntimeCommand, result: RuntimeCommandResult) -> None:
        record.exit_code = result.exit_code
        record.stdout = result.stdout
        record.stderr = result.stderr
        record.status = "completed" if result.exit_code == 0 else "failed"
        record.completed_at = datetime.now(UTC)

    def _append_event(self, runtime: WorkspaceRuntime, event_type: str, message: str) -> None:
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                created_at=datetime.now(UTC),
            )
        )

    def _require_container(self, runtime: WorkspaceRuntime) -> None:
        if not runtime.docker_container_id:
            raise ValueError("Runtime has no Docker container")
