"""Transport-independent pagination input shared by services and repositories."""

from dataclasses import dataclass

MAX_PAGE_LIMIT = 100
DEFAULT_PAGE_LIMIT = 50


@dataclass(frozen=True)
class PageParams:
    limit: int = DEFAULT_PAGE_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > MAX_PAGE_LIMIT:
            raise ValueError(f"Pagination limit must be between 1 and {MAX_PAGE_LIMIT}")
        if self.offset < 0:
            raise ValueError("Pagination offset must be non-negative")
