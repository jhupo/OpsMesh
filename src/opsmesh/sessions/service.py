import hashlib
import json
from typing import Any

from opsmesh_sdk import InvocationSource, UserIdentity
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsmesh.infra.database import new_id
from opsmesh.shared.errors import ConflictError, NotFoundError

from .models import AgentSession


def bind_session(
    db: Session,
    snapshot: dict[str, Any],
    user: UserIdentity,
    source: InvocationSource | None,
    requested_id: str | None,
) -> AgentSession:
    if requested_id is not None:
        row = db.get(AgentSession, requested_id)
        if (
            row is None
            or row.caller_id != user.user_id
            or row.agent_key != snapshot["id"]
            or row.channel_id
        ):
            raise NotFoundError("Session not found")
    elif source is not None and source.channel_id and source.conversation_id:
        scope = source.reply_scope
        if scope not in {"group", "private"}:
            raise ValueError("Unknown conversation scope")
        caller = user.user_id if scope == "private" else None
        key = hashlib.sha256(
            json.dumps(
                [
                    snapshot["id"],
                    source.channel_id,
                    scope,
                    source.conversation_id,
                    caller,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        db.execute(
            insert(AgentSession)
            .values(
                id=new_id(),
                owner_id=snapshot["owner_id"],
                caller_id=caller,
                agent_key=snapshot["id"],
                binding_key=key,
                scope=scope,
                channel_id=source.channel_id,
                conversation_id=source.conversation_id,
            )
            .on_conflict_do_nothing(index_elements=[AgentSession.binding_key])
        )
        row = db.scalar(select(AgentSession).where(AgentSession.binding_key == key))
        assert row is not None
    else:
        row = AgentSession(
            id=new_id(),
            owner_id=snapshot["owner_id"],
            caller_id=user.user_id,
            agent_key=snapshot["id"],
            binding_key=hashlib.sha256(new_id().encode()).hexdigest(),
            scope="private",
        )
        db.add(row)
        db.flush()
    if row.closed:
        raise ConflictError("Session is closed; create a new session explicitly")
    return row


def release_session(db: Session, task_id: str, session_id: str | None) -> None:
    if session_id is not None:
        row = db.get(AgentSession, session_id, with_for_update=True)
        if row is not None and row.active_task_id == task_id:
            row.active_task_id = None
