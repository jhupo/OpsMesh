"""Private Session authorization, idle checks and native-history maintenance."""

import asyncio
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import get_instance
from opsmesh.execution.runs.models import ACTIVE, ExecutionRun
from opsmesh.execution.sessions.models import ExecutionSession
from opsmesh.execution.sessions.storage import native_session
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.access import CurrentUser, ensure_active_user
from opsmesh.users.service import load_identity


def get_session(
    session: Session,
    *,
    actor: CurrentUser,
    session_id: UUID,
    lock: bool = False,
) -> ExecutionSession:
    current = load_identity(session, actor.user_id)
    if current is None:
        raise NotFoundError("The execution user does not exist")
    ensure_active_user(current)
    query = select(ExecutionSession).where(
        ExecutionSession.id == session_id,
        ExecutionSession.user_id == actor.user_id,
        ExecutionSession.status != "deleted",
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The private session does not exist")
    return row


def create_session(session: Session, *, actor: CurrentUser, instance_id: UUID) -> ExecutionSession:
    get_instance(session, actor=actor, instance_id=instance_id, lock=True)
    row = ExecutionSession(user_id=actor.user_id, instance_id=instance_id)
    session.add(row)
    session.flush()
    return row


def require_idle(session: Session, conversation: ExecutionSession) -> None:
    if session.scalar(
        select(
            exists().where(
                ExecutionRun.session_id == conversation.id,
                ExecutionRun.status.in_(ACTIVE),
            )
        )
    ):
        raise ConflictError("The private session has queued or unfinished runs")


def reset_session(
    session: Session, *, actor: CurrentUser, session_id: UUID, history_root: Path
) -> ExecutionSession:
    row = get_session(session, actor=actor, session_id=session_id, lock=True)
    require_idle(session, row)
    history = native_session(history_root, row.user_id, row.id, row.generation)
    try:
        asyncio.run(history.clear_session())
    finally:
        history.close()
    row.generation += 1
    session.flush()
    return row


def delete_session(
    session: Session, *, actor: CurrentUser, session_id: UUID, history_root: Path
) -> None:
    row = reset_session(session, actor=actor, session_id=session_id, history_root=history_root)
    row.status = "deleted"


def read_history(
    session: Session, *, actor: CurrentUser, session_id: UUID, history_root: Path, limit: int
) -> list[dict[str, object]]:
    row = get_session(session, actor=actor, session_id=session_id, lock=True)
    history = native_session(history_root, row.user_id, row.id, row.generation)
    try:
        return cast("list[dict[str, object]]", asyncio.run(history.get_items(limit)))
    finally:
        history.close()
