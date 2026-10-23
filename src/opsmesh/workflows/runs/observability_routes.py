"""HTTP routes for resumable Workflow events with live session authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from opsmesh.execution.runtime.routes import secret_cipher
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.eventstreams import EventBatch, stream_events
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession, get_auth_service
from opsmesh.workflows.runs.access import get_run
from opsmesh.workflows.runs.models import WorkflowEvent

router = APIRouter(prefix="/workflows/runs", tags=["workflow-events"])


@router.get("/{run_id}/events/stream", operation_id="workflows.runs.events.stream")
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
    token, auth = UUID(request.headers["X-Session-ID"]), get_auth_service(request)

    def fetch(cursor: int) -> EventBatch:
        with sessions.begin() as current:
            actor = auth.authenticate(current, token=token)
            row = get_run(current, actor=actor, run_id=run_id)
            rows = current.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.run_id == run_id, WorkflowEvent.sequence > cursor)
                .order_by(WorkflowEvent.sequence)
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
                row.status in {"completed", "failed", "cancelled", "needs_attention"},
            )

    return StreamingResponse(
        stream_events(request, fetch, after=max(after, last_event_id or 0)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
