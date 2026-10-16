"""FastAPI assembly with process lifecycle and reverse-order resource release."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.base import RequestResponseEndpoint

from opsmesh.bootstrap.request_context import RequestContextMiddleware
from opsmesh.bootstrap.resources import (
    build_api_resources,
    build_json_cache,
    secret_cipher,
)
from opsmesh.bootstrap.routers import register_routers
from opsmesh.config import Settings
from opsmesh.shared.db.session import create_database_engine
from opsmesh.shared.http.errors import ErrorResponse, register_error_handlers
from opsmesh.shared.http.middleware import SecurityHeadersMiddleware
from opsmesh.shared.redis.client import close_redis_client
from opsmesh.shared.version import package_version
from opsmesh.telemetry.context import capture_trace_context

ERROR_RESPONSE_STATUS_CODES = (400, 401, 403, 404, 409, 422)


def create_event_loop() -> asyncio.AbstractEventLoop:
    """Uvicorn loop factory compatible with psycopg async connections on Windows."""
    return asyncio.SelectorEventLoop()


def create_app(settings: Settings, *, engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = (
            engine
            if engine is not None
            else create_database_engine(settings.database_url, pool_size=5, max_overflow=5)
        )
        cipher = secret_cipher(settings)
        app.state.database_sessions = sessionmaker(
            database,
            expire_on_commit=False,
            info={"dispatch_cipher": cipher, "dispatch_trace_context": capture_trace_context},
        )
        app.state.secret_cipher = cipher
        app.state.storage_root = settings.storage_root
        app.state.trusted_proxy_hops = settings.trusted_proxy_hops
        app.state.trusted_proxy_networks = settings.trusted_proxy_networks
        redis_client: Redis[str] | None = None
        try:
            api_resources = build_api_resources(settings)
            redis_client = api_resources.redis_client
            app.state.redis_client = redis_client
            app.state.rate_limiter = api_resources.rate_limiter
            app.state.json_cache = build_json_cache(
                redis_client, key_prefix=settings.redis_key_prefix
            )
            yield
        finally:
            # Reverse acquisition order: release later resources before earlier ones.
            app.state.redis_client = None
            app.state.json_cache = None
            if redis_client is not None:
                close_redis_client(redis_client)
            if engine is None:
                database.dispose()

    app = FastAPI(
        title="OpsMesh",
        version=package_version(),
        lifespan=lifespan,
        responses={code: {"model": ErrorResponse} for code in ERROR_RESPONSE_STATUS_CODES},
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RequestContextMiddleware,
        slow_log_threshold_ms=settings.request_slow_log_threshold_ms,
    )
    register_error_handlers(app)
    register_routers(app, prefix=settings.api_prefix)

    @app.middleware("http")
    async def prevent_sensitive_response_caching(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
