"""Model session and native history APIs.

Every route authorizes the current principal against the session's stored
scope through the injected admission, and the run guard is read from the
assembled application state as well; until they are assembled the scope
admission refuses and the gateway answers 503 instead of trusting an
unauthenticated caller. A history mutation requires a stable idempotency
key, because its effect must survive a client retry exactly once.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.orchestration.contracts import (
    ExecutionSubject,
    RejectingScopeAdmission,
    ScopeAdmission,
)
from opsmesh.orchestration.sessions import service
from opsmesh.orchestration.sessions.access import session_scope_of
from opsmesh.orchestration.sessions.contracts import (
    HistoryOperation,
    RejectingSessionRunActivity,
    SessionRetention,
    SessionRunActivity,
)
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.orchestration.sessions.schemas import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    HistoryAppendRequest,
    HistoryAppendView,
    HistoryItemView,
    HistoryPage,
    SessionCreateRequest,
    SessionView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/orchestration/sessions", tags=["orchestration"])

DbSession = Annotated[Session, Depends(get_db_session)]

MAX_IDEMPOTENCY_KEY_LENGTH = 256
IdempotencyKey = Annotated[str, Query(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)]
HistoryLimit = Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)]


def _subject(user: CurrentUserDep) -> ExecutionSubject:
    return ExecutionSubject(user_id=user.user_id)


def _scope_admission(request: Request) -> ScopeAdmission:
    admission = getattr(request.app.state, "execution_scope_admission", None)
    if isinstance(admission, ScopeAdmission):
        return admission
    return RejectingScopeAdmission()


def _run_activity(request: Request) -> SessionRunActivity:
    activity = getattr(request.app.state, "session_run_activity", None)
    if isinstance(activity, SessionRunActivity):
        return activity
    return RejectingSessionRunActivity()


def _history_gateway(request: Request) -> HistoryGateway:
    gateway = getattr(request.app.state, "history_gateway", None)
    if isinstance(gateway, HistoryGateway):
        return gateway
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The native history gateway is not assembled",
    )


def _sessions(request: Request) -> sessionmaker[Session]:
    sessions = getattr(request.app.state, "database_sessions", None)
    if isinstance(sessions, sessionmaker):
        return sessions
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The database session factory is not assembled",
    )


def _session_view(record: SessionRecord) -> SessionView:
    return SessionView(
        session_id=record.id,
        scope_id=record.scope_id,
        principal_id=record.principal_id,
        configuration_id=record.configuration_id,
        owner_ref=record.owner_ref,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post(
    "",
    response_model=SessionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="orchestration.sessions.create",
)
def create_session(
    payload: SessionCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> SessionView:
    """Open one model session in the requested scope."""
    scope = session_scope_of(configuration_id=payload.configuration_id)
    with session_transaction(session):
        record = service.create_session(
            session,
            subject=_subject(user),
            scope=scope,
            admission=_scope_admission(request),
        )
    return _session_view(record)


@router.get("/{session_id}", response_model=SessionView, operation_id="orchestration.sessions.get")
def get_session(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> SessionView:
    """Read one model session of the current principal."""
    return _session_view(
        service.get_session(
            session,
            subject=_subject(user),
            admission=_scope_admission(request),
            session_id=session_id,
        )
    )


@router.post(
    "/{session_id}/close", response_model=SessionView, operation_id="orchestration.sessions.close"
)
def close_session(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> SessionView:
    """Close one model session, draining behind its active runs."""
    with session_transaction(session):
        record = service.close_session(
            session,
            subject=_subject(user),
            admission=_scope_admission(request),
            activity=_run_activity(request),
            session_id=session_id,
        )
    return _session_view(record)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="orchestration.sessions.delete",
)
async def delete_session(
    session_id: UUID,
    user: CurrentUserDep,
    request: Request,
) -> None:
    """Delete one closed model session together with its native history."""
    retention = getattr(request.app.state, "session_run_activity", None)
    if not isinstance(retention, SessionRetention):
        raise HTTPException(503, "Session retention authorization is not assembled")
    await service.delete_session(
        _sessions(request),
        _history_gateway(request),
        subject=_subject(user),
        admission=_scope_admission(request),
        session_id=session_id,
        retention=retention,
    )


@router.get(
    "/{session_id}/history",
    response_model=HistoryPage,
    operation_id="orchestration.sessions.history.list",
)
async def list_history(
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: HistoryLimit = DEFAULT_HISTORY_LIMIT,
) -> HistoryPage:
    """Read the native history of one visible session, oldest first."""
    items = await service.read_history(
        session,
        _history_gateway(request),
        subject=_subject(user),
        admission=_scope_admission(request),
        session_id=session_id,
        limit=limit,
    )
    return HistoryPage(items=items)


@router.post(
    "/{session_id}/history/items",
    response_model=HistoryAppendView,
    operation_id="orchestration.sessions.history.append",
)
async def append_history(
    session_id: UUID,
    payload: HistoryAppendRequest,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> HistoryAppendView:
    """Append items to the native history of one open session."""
    result = await service.operate_history(
        _sessions(request),
        _history_gateway(request),
        subject=_subject(user),
        admission=_scope_admission(request),
        activity=_run_activity(request),
        session_id=session_id,
        operation=HistoryOperation(method="add_items", items=tuple(payload.items)),
        operation_id=idempotency_key,
    )
    appended = result.appended if result.appended is not None else 0
    return HistoryAppendView(appended=appended)


@router.post(
    "/{session_id}/history/pop",
    response_model=HistoryItemView,
    operation_id="orchestration.sessions.history.pop",
)
async def pop_history(
    session_id: UUID,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> HistoryItemView:
    """Remove and return the newest history item of one open session."""
    result = await service.operate_history(
        _sessions(request),
        _history_gateway(request),
        subject=_subject(user),
        admission=_scope_admission(request),
        activity=_run_activity(request),
        session_id=session_id,
        operation=HistoryOperation(method="pop_item"),
        operation_id=idempotency_key,
    )
    return HistoryItemView(item=result.item)


@router.post(
    "/{session_id}/history/clear",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="orchestration.sessions.history.clear",
)
async def clear_history(
    session_id: UUID,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> None:
    """Clear the native history of one open session."""
    await service.operate_history(
        _sessions(request),
        _history_gateway(request),
        subject=_subject(user),
        admission=_scope_admission(request),
        activity=_run_activity(request),
        session_id=session_id,
        operation=HistoryOperation(method="clear_session"),
        operation_id=idempotency_key,
    )
