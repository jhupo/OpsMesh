"""HTTP pagination parameter parsing and stable page responses."""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

from opsmesh.shared import pagination
from opsmesh.shared.pagination import MAX_PAGE_LIMIT

LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


class PageResponse[T](BaseModel):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


def pagination_params(limit: LimitQuery = 50, offset: OffsetQuery = 0) -> pagination.PageParams:
    return pagination.PageParams(limit=limit, offset=offset)
