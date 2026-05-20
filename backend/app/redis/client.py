from __future__ import annotations

from redis import Redis
from redis.connection import ConnectionPool

from backend.app.core.config import Settings, get_settings


def create_redis_client(settings: Settings) -> Redis[str]:
    pool = ConnectionPool.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
        health_check_interval=settings.redis_health_check_interval_seconds,
    )
    return Redis(connection_pool=pool)


def close_redis_client(client: Redis[str]) -> None:
    client.close()
    client.connection_pool.disconnect()


redis_client = create_redis_client(get_settings())
