"""Map an authorized conversation scope to native execution history."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.storage.models import GatewaySession
from opsmesh.shared.errors import ConflictError


def mapped_session(
    session: Session, turn: GatewayTurn, create: Callable[[], UUID], *, fresh: bool = False
) -> UUID:
    key = turn.source.session_scope
    assert key is not None
    row = session.get(GatewaySession, key, with_for_update=True)
    if row is not None and row.user_id != turn.user_id:
        raise ConflictError("Conversation mapping belongs to a previous account binding")
    if row is None:
        row = GatewaySession(
            scope_key=key,
            user_id=turn.user_id,
            session_id=create(),
        )
        session.add(row)
    elif fresh:
        row.session_id = create()
    return row.session_id
