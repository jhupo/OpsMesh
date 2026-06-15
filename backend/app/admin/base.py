from __future__ import annotations

from typing import TypeVar

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.redis.keys import RedisKeyBuilder

T = TypeVar("T")


class AdminSessionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _count(self, statement: Select[tuple[T]]) -> int:
        count_statement = select(func.count()).select_from(statement.subquery())
        return int(self._session.scalar(count_statement) or 0)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


class AdminRedisService(AdminSessionService):
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        super().__init__(session)
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def _count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))
