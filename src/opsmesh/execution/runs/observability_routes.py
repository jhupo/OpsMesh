"""HTTP routes for owned effect traces and a resumable event feed."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from opsmesh.execution.runs.access import get_run
from opsmesh.execution.runs.models import ExecutionEvent, ExecutionOperation
from opsmesh.execution.runtime.routes import secret_cipher
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.eventstreams import EventBatch, stream_events
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession, get_auth_service

router = APIRouter(prefix="/execution/runs", tags=["execution-events"])


@router.get("/{run_id}/trace", operation_id="execution.runs.trace")
def trace(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    run = get_run(session, actor=user, run_id=run_id)
    operations = session.scalars(
        select(ExecutionOperation)
        .where(ExecutionOperation.run_id == run_id)
        .order_by(ExecutionOperation.created_at, ExecutionOperation.id)
        .limit(limit)
        .offset(offset)
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "driver": run.driver,
        "instance_id": run.instance_id,
        "attempt_id": run.attempt_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "operations": [
            {
                "id": r.id,
                "kind": r.kind,
                "status": r.status,
                "started_at": r.created_at,
                "updated_at": r.updated_at,
                "duration_seconds": (r.updated_at - r.created_at).total_seconds()
                if r.status != "started"
                else None,
                "has_result": r.encrypted_result is not None,
                "reconciliation": r.reconciliation,
            }
            for r in operations
        ],
    }


@router.get("/{run_id}/operations/result", operation_id="execution.runs.operations.result")
def operation_result(
    run_id: UUID, operation_id: str, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    get_run(session, actor=user, run_id=run_id)
    row = session.get(ExecutionOperation, operation_id)
    if row is None or row.run_id != run_id:
        raise HTTPException(404, "The operation does not exist")
    if row.encrypted_result is None:
        raise HTTPException(409, "The operation has no confirmed result")
    return secret_cipher(request).decrypt_payload(row.encrypted_result, key_id=row.key_id)


@router.get("/{run_id}/events/stream", operation_id="execution.runs.events.stream")
def events(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> StreamingResponse:
    with session_transaction(session):
        get_run(session, actor=user, run_id=run_id)
    sessions, cipher = request.app.state.database_sessions, secret_cipher(request)
    token = UUID(request.headers["X-Session-ID"])
    auth = get_auth_service(request)

    def fetch(cursor: int) -> EventBatch:
        with sessions.begin() as current:
            actor = auth.authenticate(current, token=token)
            row = get_run(current, actor=actor, run_id=run_id)
            rows = current.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.run_id == run_id, ExecutionEvent.sequence > cursor)
                .order_by(ExecutionEvent.sequence)
                .limit(200)
            )
            return EventBatch(
                [
                    (
                        e.sequence,
                        e.kind,
                        cipher.decrypt_payload(e.encrypted_payload, key_id=e.key_id),
                    )
                    for e in rows
                ],
                row.status
                in {"completed", "failed", "cancelled", "needs_attention", "interrupted"},
            )

    return StreamingResponse(
        stream_events(request, fetch, after=max(after, last_event_id or 0)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
