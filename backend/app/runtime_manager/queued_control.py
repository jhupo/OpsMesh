from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class QueuedRuntimeControl:
    def __init__(
        self,
        *,
        session: Session,
        queue: RedisQueue,
        settings: Settings,
        requested_by_user_id: UUID,
    ) -> None:
        self._session = session
        self._service = RuntimeControlService(session, settings=settings)
        self._queue = queue
        self._requested_by_user_id = requested_by_user_id

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        runtime_space_id: UUID | None = None,
        network_disabled: bool = True,
    ) -> WorkspaceRuntime | None:
        runtime = self._service.queue_runtime_create(
            workspace_id=workspace_id,
            template_id=template_id,
            name=name,
            limits=limits,
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
            requested_by_user_id=self._requested_by_user_id,
        )
        if runtime is None:
            return None
        self._enqueue(
            workspace_id=workspace_id,
            runtime_id=runtime.id,
            action="create",
            routing={
                "template_id": str(template_id),
                "name": name,
                "runtime_space_id": str(runtime_space_id) if runtime_space_id is not None else None,
                "limits": _runtime_limits_routing(limits),
                "network_disabled": network_disabled,
            },
        )
        return runtime

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self._service.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        if runtime.status != "running":
            runtime.status = "starting"
            runtime.connection_status = "offline"
            self._session.commit()
        self._enqueue(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            action="start",
            routing={},
        )
        return runtime

    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self._service.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        if runtime.status == "running":
            runtime.status = "stopping"
            runtime.connection_status = "offline"
            self._session.commit()
        self._enqueue(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            action="stop",
            routing={},
        )
        return runtime

    def _enqueue(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        action: str,
        routing: dict[str, object],
    ) -> None:
        self._queue.enqueue(
            JobPayload(
                workspace_id=workspace_id,
                job_type=JobType.RUNTIME_CONTROL,
                resource_id=runtime_id,
                requested_by_user_id=self._requested_by_user_id,
                idempotency_key=f"runtime.{action}:{workspace_id}:{runtime_id}",
                routing={"action": action, **routing},
            ),
            force=action != "create",
        )


def _runtime_limits_routing(limits: RuntimeLimits | None) -> dict[str, object] | None:
    if limits is None:
        return None
    return {
        "cpu_count": limits.cpu_count,
        "memory_mb": limits.memory_mb,
        "disk_mb": limits.disk_mb,
        "timeout_seconds": limits.timeout_seconds,
        "max_output_bytes": limits.max_output_bytes,
        "max_processes": limits.max_processes,
    }
