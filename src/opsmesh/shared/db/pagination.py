"""SQL query pagination with total counts."""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from opsmesh.shared.pagination import PageParams


def page_scalars[T](
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


def page_scalars_by_offset[T](
    session: Session,
    statement: Select[tuple[T]],
    *,
    limit: int,
    offset: int,
) -> tuple[list[T], int]:
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = session.scalars(statement.limit(limit).offset(offset)).all()
    return list(rows), int(total or 0)
