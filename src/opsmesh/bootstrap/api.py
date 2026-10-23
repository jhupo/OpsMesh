"""FastAPI assembly with process lifecycle and reverse-order resource release."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from functools import partial
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from temporalio.client import Client
from temporalio.service import RPCError

from opsmesh.agents.catalog.service import ManagedCatalog, definition_snapshot
from opsmesh.agents.plugins.registry import PluginRegistry
from opsmesh.agents.plugins.routes import router as plugin_router
from opsmesh.bootstrap.automation import dispatch_evaluation, target_ports
from opsmesh.bootstrap.catalog import load_catalog
from opsmesh.bootstrap.connections import (
    build_api_resources,
    build_database_resources,
)
from opsmesh.bootstrap.execution import configure_execution, native_bindings_factory
from opsmesh.bootstrap.gateway import GatewayExecution
from opsmesh.bootstrap.http import RequestContextMiddleware, gateway_identifier, gateway_policy
from opsmesh.bootstrap.metrics import product_metrics
from opsmesh.bootstrap.plugins import create_plugin_manager, watch_plugins
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.bootstrap.routers import register_routers
from opsmesh.bootstrap.telemetry import configure_api_telemetry, flush_process_telemetry
from opsmesh.bootstrap.workflows import configure_workflows
from opsmesh.config import Settings, get_settings
from opsmesh.gateway import Gateway
from opsmesh.gateway.api.routes import router as gateway_router
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.platform.audit.service import SecurityAuditRecorder
from opsmesh.platform.evaluations.contracts import EvaluatorRegistry
from opsmesh.platform.evaluations.evaluators import default_evaluators
from opsmesh.plugins.manager import PluginSnapshot
from opsmesh.plugins.routes import router as plugins_router
from opsmesh.resources.layout import StorageLayout
from opsmesh.resources.storage.local import LocalFileStorage
from opsmesh.shared.http.errors import ErrorResponse, register_error_handlers
from opsmesh.shared.http.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from opsmesh.shared.redis.client import close_redis_client
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.version import package_version
from opsmesh.telemetry.logging import configure_logging, shutdown_logging
from opsmesh.telemetry.metrics import start_metrics_server
from opsmesh.users.auth.service import AuthService

ERROR_RESPONSE_STATUS_CODES = (400, 401, 403, 404, 409, 422)


def temporal_health_probe(settings: Settings) -> Callable[[], Awaitable[bool]]:
    client: Client | None = None

    async def probe() -> bool:
        nonlocal client
        try:
            async with asyncio.timeout(3):
                if client is None:
                    client = await Client.connect(
                        settings.temporal_address,
                        namespace=settings.temporal_namespace,
                        tls=settings.temporal_tls_enabled,
                        api_key=settings.temporal_api_key,
                        lazy=True,
                    )
                return await client.service_client.check_health(timeout=timedelta(seconds=2))
        except (RPCError, RuntimeError, TimeoutError, OSError):
            return False

    return probe


def assemble_feature_services(app: FastAPI, settings: Settings, cipher: SecretCipher) -> None:
    app.state.settings = settings
    app.state.secret_cipher = cipher
    app.state.automation_targets = target_ports(cipher)
    app.state.evaluation_dispatch = dispatch_evaluation
    app.state.evaluation_evaluators = default_evaluators()
    app.state.redaction_rules = PLATFORM_REDACTION
    layout = StorageLayout(Path(settings.storage_root), settings.plugins_root)
    app.state.storage_layout = layout
    app.state.resource_filesystem = LocalFileStorage(layout.directories)
    app.state.auth_service = AuthService(
        security_events=SecurityAuditRecorder(),
        session_ttl_seconds=settings.session_ttl_seconds,
        token_limit=settings.default_user_token_limit,
        capacity_limits={
            "concurrency": settings.default_user_concurrency_limit,
            "environment": settings.default_user_environment_limit,
        },
    )
    app.state.temporal_health_probe = temporal_health_probe(settings)
    app.state.execution_sandbox_images = frozenset(settings.execution_sandbox_images)


def create_app(
    settings: Settings | None = None, *, evaluators: EvaluatorRegistry | None = None
) -> FastAPI:
    """Assemble the application; without settings the process configuration is read."""
    app_settings = settings if settings is not None else get_settings()
    catalog = load_catalog(app_settings.agent_catalog_path)
    # Unified logging comes first: it resets the root handlers, so the OTLP log
    # handler installed by the telemetry setup below survives and every log
    # record reaches both the bounded stdout queue and the log exporter.
    configure_logging(
        level=app_settings.log_level,
        format=app_settings.log_format,
        queue_capacity=app_settings.log_queue_capacity,
        service_name=app_settings.service_name,
        environment=app_settings.environment,
        redaction=PLATFORM_REDACTION,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with AsyncExitStack() as resources:
                database = build_database_resources(app_settings)
                resources.callback(database.engine.dispose)
                app.state.database_engine = database.engine
                app.state.database_sessions = database.sessions
                app.state.secret_cipher = database.cipher
                app.state.storage_root = app_settings.storage_root
                app.state.trusted_proxy_hops = app_settings.trusted_proxy_hops
                app.state.trusted_proxy_networks = app_settings.trusted_proxy_networks
                assemble_feature_services(app, app_settings, database.cipher)
                if evaluators is not None:
                    app.state.evaluation_evaluators = evaluators
                api_resources = build_api_resources(app_settings)
                redis_client = api_resources.redis_client
                resources.callback(close_redis_client, redis_client)
                app.state.redis_client = redis_client
                app.state.rate_limiter = api_resources.rate_limiter
                layout = app.state.storage_layout
                plugins = create_plugin_manager(
                    app_settings, database.cipher, role="api", layout=layout
                )
                resources.callback(plugins.close)
                await plugins.reload()
                app.state.plugins = plugins
                app.state.agent_plugins = plugins.current.views["agents"]
                managed = ManagedCatalog(database.sessions, database.cipher)
                await asyncio.to_thread(
                    managed.seed, catalog.agents.list(), catalog.providers.list()
                )
                configure_execution(
                    app,
                    sessions=database.sessions,
                    cipher=database.cipher,
                    definitions=lambda session, actor: definition_snapshot(
                        session, database.cipher, actor
                    ),
                    history_root=layout.history,
                    bindings=native_bindings_factory(
                        database.sessions,
                        database.cipher,
                        plugins=lambda: app.state.agent_plugins,
                        providers=managed.providers,
                        credentials=managed.credential,
                    ),
                )
                execution = GatewayExecution(
                    database.sessions, database.cipher, layout.history, layout.gateway_media
                )
                gateway = Gateway(
                    registry=plugins.current.views["channels"],
                    store=GatewayStore(database.sessions, database.cipher),
                    media_root=layout.gateway_media,
                    execution=execution,
                )
                app.state.gateway = gateway
                resources.push_async_callback(gateway.stop)

                async def apply_plugins(snapshot: PluginSnapshot) -> None:
                    await gateway.replace_registry(snapshot.views["channels"])
                    app.state.agent_plugins = snapshot.views["agents"]

                plugins.apply = apply_plugins
                watch_plugins(plugins, resources)
                if app_settings.metrics_port is not None:
                    exporter = start_metrics_server(
                        host=app_settings.metrics_host,
                        port=app_settings.metrics_port,
                        producer=partial(product_metrics, database.sessions),
                    )
                    resources.push_async_callback(asyncio.to_thread, exporter.close)
                    app.state.metrics_server = exporter
                yield
        finally:
            for name in (
                "redis_client",
                "rate_limiter",
                "settings",
                "auth_service",
                "metrics_server",
                "gateway",
                "plugins",
                "storage_layout",
                "resource_filesystem",
            ):
                setattr(app.state, name, None)
            # The logging listener is the first acquired resource: every failure
            # after its configuration must still drain and stop it.
            flush_process_telemetry()
            shutdown_logging()

    app = FastAPI(
        title="OpsMesh",
        version=package_version(),
        lifespan=lifespan,
        docs_url="/docs" if app_settings.enable_api_docs else None,
        redoc_url="/redoc" if app_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_api_docs else None,
        responses={code: {"model": ErrorResponse} for code in ERROR_RESPONSE_STATUS_CODES},
    )
    app.state.redis_client = None
    app.state.rate_limiter = None
    app.state.settings = None
    app.state.auth_service = None
    app.state.agent_plugins = PluginRegistry()
    configure_workflows(app)
    app.state.plugins = None
    app.state.gateway = None
    # Later registrations wrap earlier ones: CORS answers preflights outside the
    # gateway, request correlation precedes rate limiting, security headers sit
    # closest to the application.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=_gateway_limiter,
        policy_for_path=gateway_policy(app_settings),
        enabled=app_settings.api_rate_limit_enabled,
        window_seconds=app_settings.api_rate_limit_window_seconds,
        identifier=partial(gateway_identifier, settings=app_settings),
    )
    app.add_middleware(
        RequestContextMiddleware,
        slow_log_threshold_ms=app_settings.request_slow_log_threshold_ms,
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
    register_routers(app, prefix=app_settings.api_prefix)
    app.include_router(plugin_router, prefix=app_settings.api_prefix)
    app.include_router(plugins_router, prefix=app_settings.api_prefix)
    app.include_router(gateway_router, prefix=app_settings.api_prefix)
    configure_api_telemetry(app, app_settings)

    @app.middleware("http")
    async def prevent_sensitive_response_caching(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _gateway_limiter(request: Request) -> FixedWindowRateLimiter:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if not isinstance(limiter, FixedWindowRateLimiter):
        raise HTTPException(status_code=503, detail="Request gateway is unavailable")
    return limiter
