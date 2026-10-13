from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectionPoolRecommendation:
    cpu_count: int
    database_pool_size: int
    database_max_overflow: int
    redis_max_connections: int

    def as_dict(self) -> dict[str, int]:
        return {
            "cpu_count": self.cpu_count,
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "redis_max_connections": self.redis_max_connections,
        }


def recommend_connection_pools(cpu_count: int | None = None) -> ConnectionPoolRecommendation:
    cpus = max(1, int(cpu_count or os.cpu_count() or 1))
    database_pool_size = _bounded(cpus * 2, minimum=5, maximum=32)
    database_max_overflow = _bounded(database_pool_size, minimum=5, maximum=64)
    redis_max_connections = _bounded(database_pool_size * 3, 16, 128)
    return ConnectionPoolRecommendation(
        cpu_count=cpus,
        database_pool_size=database_pool_size,
        database_max_overflow=database_max_overflow,
        redis_max_connections=redis_max_connections,
    )


def _bounded(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)
