from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.errors import register_error_handlers
from backend.app.api.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from backend.app.api.router import api_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.executors import shutdown_blocking_executor
from backend.app.core.logging import configure_logging
from backend.app.db.session import engine
from backend.app.observability.tracing import configure_api_telemetry
from backend.app.rate_limits.service import FixedWindowRateLimiter
from backend.app.redis.client import close_redis_client, create_redis_client


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    redis_client = create_redis_client(app_settings)
    limiter = FixedWindowRateLimiter.from_redis(
        redis_client,
        key_prefix=app_settings.redis_key_prefix,
    )
    return create_app_with_dependencies(
        settings=app_settings,
        rate_limiter=limiter,
        redis_client=redis_client,
    )


def create_app_with_dependencies(
    *,
    settings: Settings,
    rate_limiter: FixedWindowRateLimiter,
    redis_client: object | None = None,
) -> FastAPI:
    app_settings = settings
    configure_logging(app_settings)

    app = FastAPI(
        title="OpsMesh API",
        version="0.1.0",
        docs_url="/docs" if app_settings.enable_api_docs else None,
        redoc_url="/redoc" if app_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_api_docs else None,
        lifespan=_lifespan,
    )
    app.state.settings = app_settings
    app.state.redis_client = redis_client
    app.state.rate_limiter = rate_limiter
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.add_middleware(
        RateLimitMiddleware,
        settings=app_settings,
        limiter=rate_limiter,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, settings=app_settings)
    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_error_handlers(app)
    app.include_router(api_router, prefix=app_settings.api_prefix)
    app.state.telemetry_runtime = configure_api_telemetry(
        app,
        app_settings,
        engine=engine,
    )
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is not None:
            close_redis_client(redis_client)
        telemetry_runtime = getattr(app.state, "telemetry_runtime", None)
        if telemetry_runtime is not None:
            telemetry_runtime.shutdown()
        shutdown_blocking_executor(wait=False)


app = create_app()
