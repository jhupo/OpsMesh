"""Redis client creation and connection pool cleanup."""

from __future__ import annotations

from redis import Redis

DEFAULT_SOCKET_TIMEOUT_SECONDS = 5.0
DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 30


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
