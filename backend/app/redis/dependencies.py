from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from redis import Redis

from backend.app.redis.client import redis_client

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


def get_redis_client(_: Request) -> RedisClient:
    return redis_client
