from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams

T = TypeVar("T")


def page_scalars(
    session: Session,
    statement: Select[tuple[T]],
    page: PageParams,
) -> tuple[list[T], int]:
    return page_scalars_by_offset(
        session,
        statement,
        limit=page.limit,
        offset=page.offset,
    )


def page_scalars_by_offset(
    session: Session,
    statement: Select[tuple[T]],
    *,
    limit: int,
    offset: int,
) -> tuple[list[T], int]:
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = session.scalars(statement.limit(limit).offset(offset)).all()
    return list(rows), int(total or 0)
