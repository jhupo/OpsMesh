"""Build stable paginated HTTP responses from domain items."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from opsmesh.shared.http.pagination import PageResponse
from opsmesh.shared.pagination import PageParams


def page_response[ResponseT: BaseModel](
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
