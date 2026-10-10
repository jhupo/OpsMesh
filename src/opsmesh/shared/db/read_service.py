from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.pagination import PageParams

T = TypeVar("T")


class AdminSessionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _count(self, statement: Select[tuple[T]]) -> int:
        count_statement = select(func.count()).select_from(statement.subquery())
        return int(self._session.scalar(count_statement) or 0)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
