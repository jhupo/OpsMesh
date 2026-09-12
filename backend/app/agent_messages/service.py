from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import Session

from backend.app.agent_messages.inbox import AgentMailboxInboxMixin
from backend.app.agent_messages.messages import AgentMailboxMessageMixin
from backend.app.agent_messages.queries import AgentMailboxQueryMixin
from backend.app.agent_messages.summary import AgentMailboxSummaryMixin
from backend.app.agent_messages.threads import AgentMailboxThreadMixin
from backend.app.agent_messages.validation import AgentMailboxValidationMixin
from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars

T = TypeVar("T")


class AgentMailboxService(
    AgentMailboxValidationMixin,
    AgentMailboxQueryMixin,
    AgentMailboxThreadMixin,
    AgentMailboxMessageMixin,
    AgentMailboxInboxMixin,
    AgentMailboxSummaryMixin,
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
