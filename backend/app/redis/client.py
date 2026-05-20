from __future__ import annotations

from dataclasses import dataclass

from redis import Redis
from redis.connection import ConnectionPool

from backend.app.core.config import Settings, get_settings


@dataclass(frozen=True)
class RedisPoolSnapshot:
    max_connections: int | None
    created_connections: int | None
    available_connections: int | None
    in_use_connections: int | None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "max_connections": self.max_connections,
            "created_connections": self.created_connections,
            "available_connections": self.available_connections,
            "in_use_connections": self.in_use_connections,
        }


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


def redis_pool_snapshot(client: Redis[str]) -> RedisPoolSnapshot:
    pool = client.connection_pool
    available = _pool_list_size(pool, "_available_connections")
    in_use = _pool_set_size(pool, "_in_use_connections")
    return RedisPoolSnapshot(
        max_connections=_pool_attr_int(pool, "max_connections"),
        created_connections=_pool_attr_int(pool, "_created_connections"),
        available_connections=available,
        in_use_connections=in_use,
    )


def _pool_attr_int(pool: object, attr_name: str) -> int | None:
    value = getattr(pool, attr_name, None)
    return value if isinstance(value, int) else None


def _pool_list_size(pool: object, attr_name: str) -> int | None:
    value = getattr(pool, attr_name, None)
    return len(value) if isinstance(value, list) else None


def _pool_set_size(pool: object, attr_name: str) -> int | None:
    value = getattr(pool, attr_name, None)
    return len(value) if isinstance(value, set) else None


redis_client = create_redis_client(get_settings())
