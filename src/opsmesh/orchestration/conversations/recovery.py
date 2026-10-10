"""Recover conversation wakeups lost with Redis or a crashed dispatcher."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.conversations.dispatch import request_advance
from opsmesh.orchestration.conversations.models import Conversation, ConversationTurn


class ConversationRecoveryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def recover(self, *, limit: int = 100, stale_after_seconds: int = 60) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        conversations = list(
            self.session.scalars(
                select(Conversation)
                .where(
                    Conversation.id.in_(
                        select(ConversationTurn.conversation_id).where(
                            ConversationTurn.workspace_id == Conversation.workspace_id,
                            ConversationTurn.status.not_in(("completed", "failed", "cancelled")),
                            ConversationTurn.updated_at < cutoff,
                        )
                    )
                )
                .order_by(Conversation.updated_at, Conversation.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for conversation in conversations:
            request_advance(self.session, conversation.workspace_id, conversation.id)
            conversation.updated_at = datetime.now(UTC)
        return len(conversations)
