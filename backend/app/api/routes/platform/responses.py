from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from pydantic import BaseModel

from backend.app.api.pagination import PageResponse
from backend.app.core.pagination import PageParams

ResponseT = TypeVar("ResponseT", bound=BaseModel)


def page_response(
    items: Iterable[object],
    total: int,
    page: PageParams,
    response_model: type[ResponseT],
) -> PageResponse[ResponseT]:
    return PageResponse(
        items=[response_model.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
