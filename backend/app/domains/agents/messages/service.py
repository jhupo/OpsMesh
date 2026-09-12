from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import Session

from backend.app.core.common.pagination import PageParams
from backend.app.core.db.pagination import page_scalars
from backend.app.domains.agents.messages.inbox import AgentMailboxInboxMixin
from backend.app.domains.agents.messages.messages import AgentMailboxMessageMixin
from backend.app.domains.agents.messages.queries import AgentMailboxQueryMixin
from backend.app.domains.agents.messages.summary import AgentMailboxSummaryMixin
from backend.app.domains.agents.messages.threads import AgentMailboxThreadMixin
from backend.app.domains.agents.messages.validation import AgentMailboxValidationMixin

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
