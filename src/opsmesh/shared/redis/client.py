"""Redis client creation, closing and pool snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from redis import Redis

DEFAULT_SOCKET_TIMEOUT_SECONDS = 5.0
DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 30


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


def create_redis_client(
    url: str,
    *,
    max_connections: int,
    socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
    socket_connect_timeout_seconds: float = DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS,
    health_check_interval_seconds: int = DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
) -> Redis[str]:
    return Redis.from_url(
        url,
        decode_responses=True,
        max_connections=max_connections,
        socket_timeout=socket_timeout_seconds,
        socket_connect_timeout=socket_connect_timeout_seconds,
        health_check_interval=float(health_check_interval_seconds),
    )


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
