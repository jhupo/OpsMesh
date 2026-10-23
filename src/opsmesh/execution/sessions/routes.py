"""HTTP private-session metadata and native history controls."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from opsmesh.execution.sessions import service
from opsmesh.execution.sessions.models import ExecutionSession
from opsmesh.execution.sessions.schemas import SessionCreate, SessionView
from opsmesh.resources.layout import StorageLayout
from opsmesh.shared.db.session import session_transaction
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/execution", tags=["execution"])


def _storage_layout(request: Request) -> StorageLayout:
    layout = getattr(request.app.state, "storage_layout", None)
    if not isinstance(layout, StorageLayout):
        raise HTTPException(status_code=503, detail="Resource storage is not configured")
    return layout


@router.post("/sessions", status_code=201, operation_id="execution.sessions.create")
def create_session(payload: SessionCreate, session: DbSession, user: CurrentUserDep) -> SessionView:
    with session_transaction(session):
        result = SessionView.model_validate(
            service.create_session(
                session,
                actor=user,
                instance_id=payload.instance_id,
            )
        )
    return result


@router.get("/sessions", operation_id="execution.sessions.list")
def list_sessions(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[SessionView]:
    return [
        SessionView.model_validate(row)
        for row in session.scalars(
            select(ExecutionSession)
            .where(
                ExecutionSession.user_id == user.user_id,
                ExecutionSession.status != "deleted",
            )
            .order_by(ExecutionSession.created_at.desc(), ExecutionSession.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/sessions/{session_id}", operation_id="execution.sessions.read")
def read_session(session_id: UUID, session: DbSession, user: CurrentUserDep) -> SessionView:
    return SessionView.model_validate(
        service.get_session(session, actor=user, session_id=session_id)
    )


@router.post("/sessions/{session_id}/reset", operation_id="execution.sessions.reset")
def reset_session(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> SessionView:
    with session_transaction(session):
        result = SessionView.model_validate(
            service.reset_session(
                session,
                actor=user,
                session_id=session_id,
                history_root=_storage_layout(request).history,
            )
        )
    return result


@router.delete("/sessions/{session_id}", status_code=204, operation_id="execution.sessions.delete")
def delete_session(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> None:
    with session_transaction(session):
        service.delete_session(
            session,
            actor=user,
            session_id=session_id,
            history_root=_storage_layout(request).history,
        )


@router.get("/sessions/{session_id}/history", operation_id="execution.sessions.history")
def read_history(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, object]]:
    with session_transaction(session):
        return service.read_history(
            session,
            actor=user,
            session_id=session_id,
            history_root=_storage_layout(request).history,
            limit=limit,
        )
