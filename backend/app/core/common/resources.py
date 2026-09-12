from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeResourceRecommendation:
    cpu_count: int
    database_pool_size: int
    database_max_overflow: int
    blocking_thread_pool_workers: int
    redis_max_connections: int
    worker_max_jobs: int

    def as_dict(self) -> dict[str, int]:
        return {
            "cpu_count": self.cpu_count,
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "blocking_thread_pool_workers": self.blocking_thread_pool_workers,
            "redis_max_connections": self.redis_max_connections,
            "worker_max_jobs": self.worker_max_jobs,
        }


def recommend_runtime_resources(cpu_count: int | None = None) -> RuntimeResourceRecommendation:
    cpus = max(1, int(cpu_count or os.cpu_count() or 1))
    database_pool_size = _bounded(cpus * 2, minimum=5, maximum=32)
    database_max_overflow = _bounded(database_pool_size, minimum=5, maximum=64)
    blocking_thread_pool_workers = _bounded(cpus * 4, minimum=8, maximum=64)
    redis_max_connections = _bounded(database_pool_size + blocking_thread_pool_workers, 16, 128)
    worker_max_jobs = _bounded(cpus, minimum=1, maximum=16)
    return RuntimeResourceRecommendation(
        cpu_count=cpus,
        database_pool_size=database_pool_size,
        database_max_overflow=database_max_overflow,
        blocking_thread_pool_workers=blocking_thread_pool_workers,
        redis_max_connections=redis_max_connections,
        worker_max_jobs=worker_max_jobs,
    )


def _bounded(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)
