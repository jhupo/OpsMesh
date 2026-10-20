"""User scope and explicitly shared composition authorization."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.contracts import configuration_access_for
from opsmesh.shared.errors import ForbiddenError, NotFoundError


def authorize_scope(
    session: Session,
    *,
    actor: CurrentUser,
    owner_id: UUID,
    configuration_id: UUID | None = None,
    write: bool = False,
    manage: bool = False,
    allow_archived: bool = False,
) -> None:
    if actor.status != "active":
        raise ForbiddenError("The flow principal is inactive")
    if actor.user_id != owner_id:
        raise NotFoundError("The private flow execution is unavailable")
    if configuration_id is None:
        return
    configuration_access_for(session).authorize_scope(
        session,
        actor=actor,
        configuration_id=configuration_id,
        write=write,
        manage=manage,
        allow_archived=allow_archived,
    )
