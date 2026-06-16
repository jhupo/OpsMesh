from typing import TypeVar

from sqlalchemy import Select

from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars

T = TypeVar("T")


class AgentMailboxPaginationMixin:
    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
