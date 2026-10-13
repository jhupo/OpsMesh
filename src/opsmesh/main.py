import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.resources import build_api_resources, shutdown_api_resources
from opsmesh.bootstrap.routers import api_router
from opsmesh.bootstrap.telemetry import configure_api_telemetry
from opsmesh.orchestration.tasks.async_events import create_task_event_sessions
from opsmesh.orchestration.tasks.notifications import (
    TaskEventNotificationListener,
    TaskEventNotifications,
)
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import engine
from opsmesh.shared.http.error_schemas import ErrorResponse
from opsmesh.shared.http.errors import register_error_handlers
from opsmesh.shared.http.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.telemetry.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    resources = build_api_resources(app_settings)
    return create_app_with_dependencies(
        settings=app_settings,
        rate_limiter=resources.rate_limiter,
        redis_client=resources.redis_client,
    )


def create_app_with_dependencies(
    *,
    settings: Settings,
    rate_limiter: FixedWindowRateLimiter,
    redis_client: object | None = None,
) -> FastAPI:
    register_models()
    app_settings = settings
    configure_logging(app_settings)

    app = FastAPI(
        title="OpsMesh API",
        version="0.1.0",
        docs_url="/docs" if app_settings.enable_api_docs else None,
        redoc_url="/redoc" if app_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_api_docs else None,
        responses={422: {"model": ErrorResponse, "description": "Request validation failed"}},
        lifespan=_lifespan,
    )
    app.state.settings = app_settings
    app.state.redis_client = redis_client
    app.state.temporal_client = None
    app.state.temporal_client_lock = asyncio.Lock()
    task_event_engine, task_event_sessions = create_task_event_sessions(app_settings)
    app.state.task_event_engine = task_event_engine
    app.state.task_event_sessions = task_event_sessions
    app.state.task_event_notifications = TaskEventNotifications()
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
    stop = asyncio.Event()
    listener = TaskEventNotificationListener(
        app.state.settings.database_url, app.state.task_event_notifications
    )
    listener_task = asyncio.create_task(listener.run(stop), name="task-event-notifications")
    try:
        yield
    finally:
        stop.set()
        listener_task.cancel()
        await asyncio.gather(listener_task, return_exceptions=True)
        await app.state.task_event_engine.dispose()
        shutdown_api_resources(app)


app = create_app()
