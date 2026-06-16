from sqlalchemy.orm import Session

from backend.app.agent_messages.inbox import AgentMailboxInboxMixin
from backend.app.agent_messages.messages import AgentMailboxMessageMixin
from backend.app.agent_messages.pagination import AgentMailboxPaginationMixin
from backend.app.agent_messages.queries import AgentMailboxQueryMixin
from backend.app.agent_messages.summary import AgentMailboxSummaryMixin
from backend.app.agent_messages.threads import AgentMailboxThreadMixin
from backend.app.agent_messages.validation import AgentMailboxValidationMixin


class AgentMailboxService(
    AgentMailboxPaginationMixin,
    AgentMailboxValidationMixin,
    AgentMailboxQueryMixin,
    AgentMailboxThreadMixin,
    AgentMailboxMessageMixin,
    AgentMailboxInboxMixin,
    AgentMailboxSummaryMixin,
):
    def __init__(self, session: Session) -> None:
        self._session = session
