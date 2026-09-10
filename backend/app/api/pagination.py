from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from backend.app.core import pagination

LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]
T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


def pagination_params(limit: LimitQuery = 50, offset: OffsetQuery = 0) -> pagination.PageParams:
    return pagination.PageParams(limit=limit, offset=offset)
