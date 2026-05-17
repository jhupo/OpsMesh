from fastapi import Request
from redis import Redis

from backend.app.redis.client import redis_client


def get_redis_client(_: Request) -> Redis:
    return redis_client
