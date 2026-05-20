from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request
from redis import Redis

from backend.app.core.config import Settings, get_settings
from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.client import redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


def get_redis_client(_: Request) -> RedisClient:
    app_redis_client = getattr(_.app.state, "redis_client", None)
    return app_redis_client or redis_client


_REDIS_DEPENDENCY = Depends(get_redis_client)
_SETTINGS_DEPENDENCY = Depends(get_settings)


def get_cache_service(
    redis: RedisClient = _REDIS_DEPENDENCY,
    settings: Settings = _SETTINGS_DEPENDENCY,
) -> RedisJsonCache:
    return RedisJsonCache(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
        namespace="api",
    )
