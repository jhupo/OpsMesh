from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.api.schemas.agents import (
    AgentSessionSummaryResponse,
)
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.teams import (
    AgentTeamRuntimeControlRequest,
    AgentTeamRuntimeEnsureRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.teams.execution_loop import (
    enqueue_team_execution_loop_job,
)
from backend.app.teams.workspace_service import (
    WorkspaceTeamService,
)
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

def _team_runtime_limits(request: AgentTeamRuntimeEnsureRequest) -> RuntimeLimits | None:
    if request.limits is None:
        return None
    return RuntimeLimits(
        cpu_count=request.limits.cpu_count,
        memory_mb=request.limits.memory_mb,
        disk_mb=request.limits.disk_mb,
        timeout_seconds=request.limits.timeout_seconds,
        max_output_bytes=request.limits.max_output_bytes,
        max_processes=request.limits.max_processes,
    )


def _enqueue_team_runtime_control(
    queue: RedisQueue,
    context: WorkspaceContext,
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest | AgentTeamRuntimeEnsureRequest,
    action: str,
) -> None:
    enqueue_team_execution_loop_job(
        queue=queue,
        workspace_id=context.workspace.id,
        team_id=team_id,
        requested_by_user_id=context.user.user_id,
        idempotency_suffix=f"runtime-{action}-{team_id}",
        priority=10,
        routing={
            "source": "workspace_api",
            "trigger": f"team_runtime_{action}",
            "reason": request.reason,
            "metadata": redact_sensitive_payload(request.metadata),
            "runtime_action": action,
        },
    )


class _QueuedRuntimeControl:
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
    ) -> object | None:
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

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> object | None:
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

    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> object | None:
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


def _queued_runtime_control(
    session: Session,
    queue: RedisQueue,
    settings: Settings,
    context: WorkspaceContext,
) -> _QueuedRuntimeControl:
    return _QueuedRuntimeControl(
        session=session,
        queue=queue,
        settings=settings,
        requested_by_user_id=context.user.user_id,
    )


def _require_team(
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
) -> None:
    if WorkspaceTeamService(db_session).get_team(workspace_id, team_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")


def _require_team_session(
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
    session_id: UUID,
) -> None:
    _require_team(db_session, workspace_id, team_id)
    detail = PersistentAgentSessionManagementService(db_session).get_session(
        workspace_id=workspace_id,
        session_id=session_id,
        item_limit=1,
    )
    if detail is None or detail.session.agent_team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")


def _set_team_session_status_response(
    *,
    db_session: Session,
    workspace_id: UUID,
    team_id: UUID,
    session_id: UUID,
    action: str,
) -> AgentSessionSummaryResponse:
    _require_team_session(db_session, workspace_id, team_id, session_id)
    service = PersistentAgentSessionManagementService(db_session)
    if action == "archive":
        summary = service.archive_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "freeze":
        summary = service.freeze_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "activate":
        summary = service.activate_session(workspace_id=workspace_id, session_id=session_id)
    else:
        raise ValueError(f"Unsupported session action: {action}")
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")
    db_session.commit()
    return AgentSessionSummaryResponse.model_validate(summary)


