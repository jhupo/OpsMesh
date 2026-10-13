from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


class ConversationActivities:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @activity.defn(name="opsmesh.conversation.advance")
    def advance(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.CONVERSATION:
            raise ValueError("Conversation Activity requires a conversation reference")
        with self._sessions.begin() as session:
            ConversationAdvanceService(session).advance(request.workspace_id, request.resource_id)
