"""Mapping keys isolate users, conversations and route generations."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.conversations import conversation_key
from opsmesh.gateway.models import GatewaySession
from opsmesh.shared.errors import ConflictError


def mapped_session(
    session: Session, turn: GatewayTurn, create: Callable[[], UUID], *, fresh: bool = False
) -> UUID:
    key = turn.route.id, conversation_key(turn.message)
    row = session.get(GatewaySession, key, with_for_update=True)
    if row is not None and row.user_id != turn.user_id:
        raise ConflictError("Conversation mapping belongs to a previous account binding")
    if row is None:
        row = GatewaySession(
            route_id=turn.route.id,
            conversation_key=key[1],
            user_id=turn.user_id,
            session_id=create(),
        )
        session.add(row)
    elif fresh:
        row.session_id = create()
    return row.session_id
