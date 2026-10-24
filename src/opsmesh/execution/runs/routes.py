"""Instance-based Run submission, explicit direct execution and approvals."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.runs.access import get_run
from opsmesh.execution.runs.approvals import decide_approval
from opsmesh.execution.runs.executor import RunExecutor
from opsmesh.execution.runs.models import ExecutionEvent, ExecutionRun
from opsmesh.execution.runs.reconciliation import EffectResolution, reconcile_effect
from opsmesh.execution.runs.schemas import ApprovalDecision, RunCreate, RunView
from opsmesh.execution.runs.service import cancel_run, submit_run
from opsmesh.execution.runs.streaming import RunEventRecorder
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import secret_cipher
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/execution/runs", tags=["execution-runs"])


@router.post("/{run_id}/reconciliation", operation_id="execution.runs.reconcile")
def reconcile(
    run_id: UUID,
    payload: EffectResolution,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> RunView:
    with session_transaction(session):
        return RunView.model_validate(
            reconcile_effect(
                session, actor=user, run_id=run_id, request=payload, cipher=secret_cipher(request)
            )
        )


def dispatcher(request: Request, driver: str) -> Callable[[Session, UUID], None] | None:
    dispatchers = getattr(request.app.state, "execution_run_dispatchers", {})
    if driver != "direct" and driver not in dispatchers:
        raise HTTPException(422, "The requested execution driver is not configured")
    return dispatchers.get(driver)


@router.post("", status_code=202, operation_id="execution.runs.create")
def create(
    payload: RunCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> RunView:
    dispatch = dispatcher(request, payload.driver)
    with session_transaction(session):
        row = submit_run(session, actor=user, request=payload, cipher=secret_cipher(request))
        if dispatch is not None and row.status == "queued":
            dispatch(session, row.id)
        return RunView.model_validate(row)


@router.get("", operation_id="execution.runs.list")
def list_runs(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[RunView]:
    return [
        RunView.model_validate(row)
        for row in session.scalars(
            select(ExecutionRun)
            .where(ExecutionRun.user_id == user.user_id)
            .order_by(ExecutionRun.created_at.desc(), ExecutionRun.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/{run_id}", operation_id="execution.runs.read")
def read(run_id: UUID, session: DbSession, user: CurrentUserDep) -> RunView:
    return RunView.model_validate(get_run(session, actor=user, run_id=run_id))


@router.post("/{run_id}/execute", operation_id="execution.runs.execute")
async def execute(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    stream_model: bool = False,
) -> RunView:
    executor = getattr(request.app.state, "execution_executor", None)
    if not isinstance(executor, RunExecutor):
        raise HTTPException(503, "Direct execution is not configured")
    with session_transaction(session):
        row = get_run(session, actor=user, run_id=run_id)
        if row.driver != "direct":
            raise HTTPException(409, "This run belongs to its configured orchestrator")
        if row.environment_id is not None and executor.sandbox is None:
            raise HTTPException(
                503, "Direct sandbox execution requires an authorized sandbox executor"
            )
    recorder = (
        RunEventRecorder(executor.sessions, executor.cipher, run_id) if stream_model else None
    )
    await executor.execute(run_id, on_event=recorder)
    session.expire_all()
    return RunView.model_validate(get_run(session, actor=user, run_id=run_id))


@router.post("/{run_id}/cancel", operation_id="execution.runs.cancel")
def cancel(run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request) -> RunView:
    with session_transaction(session):
        row = cancel_run(session, actor=user, run_id=run_id, cipher=secret_cipher(request))
        dispatch = getattr(request.app.state, "execution_cancel_dispatcher", None)
        if dispatch is not None:
            dispatch(session, row)
        return RunView.model_validate(row)


@router.get("/{run_id}/result", operation_id="execution.runs.result")
def result(
    run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    row = get_run(session, actor=user, run_id=run_id)
    if row.encrypted_result is None:
        raise HTTPException(409, "The run has no confirmed result")
    return secret_cipher(request).decrypt_payload(row.encrypted_result)


@router.post("/{run_id}/approval", operation_id="execution.runs.approve")
def approve(
    run_id: UUID,
    payload: ApprovalDecision,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> RunView:
    with session_transaction(session):
        row = decide_approval(
            session, actor=user, run_id=run_id, request=payload, cipher=secret_cipher(request)
        )
        dispatch = dispatcher(request, row.driver)
        if dispatch is not None:
            dispatch(session, row.id)
        return RunView.model_validate(row)


@router.get("/{run_id}/events", operation_id="execution.runs.events")
def events(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, object]]:
    get_run(session, actor=user, run_id=run_id)
    cipher = secret_cipher(request)
    return [
        {
            "sequence": row.sequence,
            "kind": row.kind,
            "payload": cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id),
        }
        for row in session.scalars(
            select(ExecutionEvent)
            .where(
                ExecutionEvent.run_id == run_id,
                ExecutionEvent.sequence > after,
            )
            .order_by(ExecutionEvent.sequence)
            .limit(limit)
        )
    ]
