from __future__ import annotations

from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.shared.db.read_service import AdminSessionService
from opsmesh.shared.redis.keys import RedisKeyBuilder


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
