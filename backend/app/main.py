from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.errors import register_error_handlers
from backend.app.api.router import api_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import configure_logging
from backend.app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from backend.app.rate_limits.service import RedisFixedWindowRateLimiter
from backend.app.redis.client import create_redis_client


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    limiter = RedisFixedWindowRateLimiter(
        create_redis_client(app_settings),
        key_prefix=app_settings.redis_key_prefix,
    )
    return create_app_with_dependencies(settings=app_settings, rate_limiter=limiter)


def create_app_with_dependencies(
    *,
    settings: Settings,
    rate_limiter: RedisFixedWindowRateLimiter,
) -> FastAPI:
    app_settings = settings
    configure_logging(app_settings)

    app = FastAPI(
        title="ChainCloud Agent Team API",
        version="0.1.0",
        docs_url="/docs" if app_settings.enable_api_docs else None,
        redoc_url="/redoc" if app_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_api_docs else None,
    )
    app.state.settings = app_settings
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, settings=app_settings)
    app.add_middleware(
        RateLimitMiddleware,
        settings=app_settings,
        limiter=rate_limiter,
    )
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
    return app


app = create_app()
