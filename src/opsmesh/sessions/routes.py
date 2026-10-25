import hashlib
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from opsmesh.agents.service import snapshot, usable
from opsmesh.contracts import UserIdentity
from opsmesh.infra.database import now
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.http import get_current_user, get_db_session, serialize_record

from .models import AgentSession
from .schemas import SessionView
from .service import bind_session

router = APIRouter(tags=["sessions"])


def accessible_session(db: Session, user: UserIdentity, session_id: str) -> AgentSession:
    row = db.get(AgentSession, session_id)
    if row is None or (not user.is_admin and user.user_id not in {row.owner_id, row.caller_id}):
        raise NotFoundError("Session not found")
    return row


@router.post("/agents/{agent_id}/sessions", status_code=201, response_model=SessionView)
def create_session(
    agent_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return serialize_record(
        bind_session(db, snapshot(usable(db, user, agent_id)), user, None, None)
    )


@router.get("/sessions", response_model=list[SessionView])
def list_sessions(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    return [
        serialize_record(row)
        for row in db.scalars(
            select(AgentSession)
            .where(
                or_(AgentSession.owner_id == user.user_id, AgentSession.caller_id == user.user_id)
            )
            .order_by(AgentSession.created_at.desc())
            .limit(200)
        )
    ]


@router.get("/sessions/{session_id}", response_model=SessionView)
def get_session(
    session_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return serialize_record(accessible_session(db, user, session_id))


@router.post("/sessions/{session_id}/close", response_model=SessionView)
def close_session(
    session_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    accessible_session(db, user, session_id)
    row = db.get(AgentSession, session_id, with_for_update=True, populate_existing=True)
    assert row is not None
    if row.active_task_id or (row.sandbox_access_until and row.sandbox_access_until > now()):
        raise ConflictError("Cancel or finish the active task before closing its session")
    row.closed = True
    row.binding_key = hashlib.sha256(f"closed:{row.id}".encode()).hexdigest()
    return serialize_record(row)
