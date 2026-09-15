from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI

from backend.app.core.config import Settings
from backend.app.core.executors import shutdown_blocking_executor
from backend.app.core.redis.client import close_redis_client, create_redis_client
from backend.app.core.security.rate_limits import FixedWindowRateLimiter


class ShutdownResource(Protocol):
    def shutdown(self) -> None: ...


@dataclass(frozen=True)
class APIResources:
    redis_client: object
    rate_limiter: FixedWindowRateLimiter


def build_api_resources(settings: Settings) -> APIResources:
    redis_client = create_redis_client(settings)
    return APIResources(
        redis_client=redis_client,
        rate_limiter=FixedWindowRateLimiter.from_redis(
            redis_client,
            key_prefix=settings.redis_key_prefix,
        ),
    )


def shutdown_api_resources(app: FastAPI) -> None:
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client is not None:
        close_redis_client(redis_client)
    telemetry_runtime: ShutdownResource | None = getattr(
        app.state,
        "telemetry_runtime",
        None,
    )
    if telemetry_runtime is not None:
        telemetry_runtime.shutdown()
    shutdown_blocking_executor(wait=False)
