"""A turn stream follows its existing Task; connecting never submits or executes work."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.conversations.schemas import ConversationTurnResponse
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.shared.errors import NotFoundError


@dataclass(frozen=True)
class TurnStreamSnapshot:
    turn: dict[str, object]
    task_id: UUID | None


def turn_stream_snapshot(
    session_factory: Callable[[], Session],
    user: AuthenticatedUser,
    workspace_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
) -> TurnStreamSnapshot:
    # Each refresh owns a short transaction/connection. Nothing stays checked out
    # during notification waiting or a slow client's response consumption.
    with session_factory() as session:
        live_user = AuthenticationService(session).refresh_authenticated_user(user)
        context = WorkspaceAuthorizationService(session).require_workspace(
            user_id=live_user.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.READ,
            authenticated_user=live_user,
        )
        service = ConversationService(session)
        service.get(context, conversation_id)
        turn = session.scalar(
            select(ConversationTurn).where(
                ConversationTurn.workspace_id == workspace_id,
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.id == turn_id,
            )
        )
        if turn is None:
            raise NotFoundError("Conversation turn not found")
        task_id = session.scalar(
            select(ConversationExecution.task_id).where(
                ConversationExecution.workspace_id == workspace_id,
                ConversationExecution.turn_id == turn_id,
            )
        )
        if task_id is not None:
            ResourceAuthorizationService(session, live_user).require(
                workspace_id,
                ResourceKind.TASK,
                task_id,
                ResourceAction.READ,
            )
        errors = service.turn_errors(context, conversation_id, [turn])
        response = ConversationTurnResponse.model_validate(turn).model_copy(
            update={"error": errors.get(turn.id)}
        )
        return TurnStreamSnapshot(response.model_dump(mode="json"), task_id)
