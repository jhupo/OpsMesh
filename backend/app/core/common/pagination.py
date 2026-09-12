"""Transport-independent pagination input shared by services and repositories."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PageParams:
    limit: int = 50
    offset: int = 0
