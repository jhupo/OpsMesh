from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import (
    TaskTransferCreateRequest,
    TaskTransferDecisionRequest,
    TaskTransferResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.tasks.transfers import (
    TaskTransferCommand,
    TaskTransferDecision,
    TaskTransferError,
    TaskTransferService,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.post(
    "/tasks/{task_id}/transfers",
    response_model=TaskTransferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_task_transfer(
    task_id: UUID,
    request: TaskTransferCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskTransferResponse:
    try:
        transfer = TaskTransferService(session).request_transfer(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            command=TaskTransferCommand(
                target_agent_profile_id=request.target_agent_profile_id,
                source_agent_profile_id=request.source_agent_profile_id,
                reason=request.reason,
                idempotency_key=request.idempotency_key or idempotency_key,
            ),
        )
    except TaskTransferError as exc:
        raise _transfer_http_error(exc) from exc
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskTransferResponse.model_validate(transfer)


@router.get("/tasks/{task_id}/transfers", response_model=list[TaskTransferResponse])
async def list_task_transfers(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[TaskTransferResponse]:
    transfers = TaskTransferService(session).list_transfers(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if transfers is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return [TaskTransferResponse.model_validate(transfer) for transfer in transfers]


@router.post(
    "/tasks/{task_id}/transfers/{transfer_id}/accept",
    response_model=TaskTransferResponse,
)
async def accept_task_transfer(
    task_id: UUID,
    transfer_id: UUID,
    request: TaskTransferDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TaskTransferResponse:
    try:
        transfer = TaskTransferService(session).accept_transfer(
            workspace_id=context.workspace.id,
            task_id=task_id,
            transfer_id=transfer_id,
            actor_user_id=context.user.user_id,
            decision=TaskTransferDecision(reason=request.reason, enqueue=request.enqueue),
            queue=queue,
        )
    except TaskTransferError as exc:
        raise _transfer_http_error(exc) from exc
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    return TaskTransferResponse.model_validate(transfer)


@router.post(
    "/tasks/{task_id}/transfers/{transfer_id}/reject",
    response_model=TaskTransferResponse,
)
async def reject_task_transfer(
    task_id: UUID,
    transfer_id: UUID,
    request: TaskTransferDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskTransferResponse:
    try:
        transfer = TaskTransferService(session).reject_transfer(
            workspace_id=context.workspace.id,
            task_id=task_id,
            transfer_id=transfer_id,
            actor_user_id=context.user.user_id,
            decision=TaskTransferDecision(reason=request.reason),
        )
    except TaskTransferError as exc:
        raise _transfer_http_error(exc) from exc
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    return TaskTransferResponse.model_validate(transfer)


def _transfer_http_error(error: TaskTransferError) -> HTTPException:
    not_found_codes = {
        "task_transfer_target_not_found",
        "task_transfer_team_not_found",
    }
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND
        if error.code in not_found_codes
        else status.HTTP_409_CONFLICT,
        detail={"code": error.code, "message": str(error)},
    )
