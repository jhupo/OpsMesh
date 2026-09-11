from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse
from backend.app.api.schemas.runtimes import (
    RuntimeCommandRequest,
    RuntimeCommandResponse,
    RuntimeCreateRequest,
    RuntimeEventResponse,
    RuntimeLimitsRequest,
    RuntimeTemplateResponse,
    WorkspaceRuntimeResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.runtime_manager.core.contracts import RuntimeLimits
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runtimes"])


@router.get("/runtime-templates", response_model=list[RuntimeTemplateResponse])
async def list_runtime_templates(
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[RuntimeTemplateResponse]:
    templates = RuntimeControlService(session, settings=settings).list_templates()
    return [RuntimeTemplateResponse.model_validate(template) for template in templates]


@router.post(
    "/runtimes",
    response_model=WorkspaceRuntimeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_runtime(
    request: RuntimeCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> WorkspaceRuntimeResponse:
    limits = _to_runtime_limits(request.limits)
    try:
        runtime = RuntimeControlService(session, settings=settings).queue_runtime_create(
            workspace_id=context.workspace.id,
            template_id=request.template_id,
            name=request.name,
            limits=limits,
            runtime_space_id=request.runtime_space_id,
            network_disabled=request.network_disabled,
            execution_mode=request.execution_mode,
            pool_key=request.pool_key,
            requested_by_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeSafetyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc
    except RuntimeQuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime template not found",
        )
    _enqueue_runtime_control(
        queue,
        JobPayload(
            workspace_id=context.workspace.id,
            job_type=JobType.RUNTIME_CONTROL,
            resource_id=runtime.id,
            requested_by_user_id=context.user.user_id,
            idempotency_key=f"runtime.create:{context.workspace.id}:{runtime.id}",
            routing={
                "action": "create",
                "template_id": str(request.template_id),
                "name": request.name,
                "runtime_space_id": str(request.runtime_space_id)
                if request.runtime_space_id is not None
                else None,
                "limits": _limits_routing(limits),
                "network_disabled": request.network_disabled,
                "execution_mode": request.execution_mode,
                "pool_key": request.pool_key,
            },
        ),
    )
    return WorkspaceRuntimeResponse.model_validate(runtime)


@router.get("/runtimes", response_model=PageResponse[WorkspaceRuntimeResponse])
async def list_runtimes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    runtime_status: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[WorkspaceRuntimeResponse]:
    items, total = RuntimeControlService(session, settings=settings).list_runtimes(
        context.workspace.id,
        limit=limit,
        offset=offset,
        status=runtime_status,
    )
    return PageResponse(
        items=[WorkspaceRuntimeResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/runtimes/{runtime_id}/start", response_model=WorkspaceRuntimeResponse)
async def start_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> WorkspaceRuntimeResponse:
    service = RuntimeControlService(session, settings=settings)
    runtime = _runtime_or_404(service.get_runtime(context.workspace.id, runtime_id))
    _enqueue_runtime_control(
        queue,
        JobPayload(
            workspace_id=context.workspace.id,
            job_type=JobType.RUNTIME_CONTROL,
            resource_id=runtime_id,
            requested_by_user_id=context.user.user_id,
            idempotency_key=f"runtime.start:{context.workspace.id}:{runtime_id}",
            routing={"action": "start"},
        ),
    )
    return runtime


@router.post("/runtimes/{runtime_id}/stop", response_model=WorkspaceRuntimeResponse)
async def stop_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> WorkspaceRuntimeResponse:
    service = RuntimeControlService(session, settings=settings)
    runtime = _runtime_or_404(service.get_runtime(context.workspace.id, runtime_id))
    _enqueue_runtime_control(
        queue,
        JobPayload(
            workspace_id=context.workspace.id,
            job_type=JobType.RUNTIME_CONTROL,
            resource_id=runtime_id,
            requested_by_user_id=context.user.user_id,
            idempotency_key=f"runtime.stop:{context.workspace.id}:{runtime_id}",
            routing={"action": "stop"},
        ),
    )
    return runtime


@router.delete("/runtimes/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime(
    runtime_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> None:
    runtime = RuntimeControlService(session, settings=settings).get_runtime(
        context.workspace.id,
        runtime_id,
    )
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
    _enqueue_runtime_control(
        queue,
        JobPayload(
            workspace_id=context.workspace.id,
            job_type=JobType.RUNTIME_CONTROL,
            resource_id=runtime_id,
            requested_by_user_id=context.user.user_id,
            idempotency_key=f"runtime.delete:{context.workspace.id}:{runtime_id}",
            routing={"action": "delete"},
        ),
        force=True,
    )


@router.post(
    "/runtimes/{runtime_id}/commands",
    response_model=RuntimeCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_runtime_command(
    runtime_id: UUID,
    request: RuntimeCommandRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> RuntimeCommandResponse:
    try:
        command = RuntimeControlService(session, settings=settings).queue_command(
            workspace_id=context.workspace.id,
            runtime_id=runtime_id,
            command=request.command,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
    if command.status == "queued":
        _enqueue_runtime_control(
            queue,
            JobPayload(
                workspace_id=context.workspace.id,
                job_type=JobType.RUNTIME_CONTROL,
                resource_id=runtime_id,
                requested_by_user_id=context.user.user_id,
                idempotency_key=f"runtime.command:{context.workspace.id}:{command.id}",
                routing={
                    "action": "command",
                    "runtime_command_id": str(command.id),
                    "command": request.command,
                },
            ),
            force=True,
        )
    return RuntimeCommandResponse.model_validate(command)


@router.get(
    "/runtimes/{runtime_id}/commands",
    response_model=PageResponse[RuntimeCommandResponse],
)
async def list_runtime_commands(
    runtime_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[RuntimeCommandResponse]:
    result = RuntimeControlService(session, settings=settings).list_commands(
        context.workspace.id,
        runtime_id,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
    items, total = result
    return PageResponse(
        items=[RuntimeCommandResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runtimes/{runtime_id}/events", response_model=PageResponse[RuntimeEventResponse])
async def list_runtime_events(
    runtime_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[RuntimeEventResponse]:
    result = RuntimeControlService(session, settings=settings).list_events(
        context.workspace.id,
        runtime_id,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
    items, total = result
    return PageResponse(
        items=[RuntimeEventResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


def _to_runtime_limits(request: RuntimeLimitsRequest | None) -> RuntimeLimits | None:
    if request is None:
        return None
    return RuntimeLimits(
        cpu_count=request.cpu_count,
        memory_mb=request.memory_mb,
        disk_mb=request.disk_mb,
        timeout_seconds=request.timeout_seconds,
        max_output_bytes=request.max_output_bytes,
        max_processes=request.max_processes,
    )


def _runtime_or_404(runtime: object | None) -> WorkspaceRuntimeResponse:
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
    return WorkspaceRuntimeResponse.model_validate(runtime)


def _limits_routing(limits: RuntimeLimits | None) -> dict[str, object] | None:
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


def _enqueue_runtime_control(
    queue: RedisQueue,
    job: JobPayload,
    *,
    force: bool = False,
) -> None:
    if not queue.enqueue(job, force=force):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime control request is already queued",
        )
