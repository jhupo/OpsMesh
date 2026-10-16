"""FastAPI assembly with process lifecycle and reverse-order resource release."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from starlette.middleware.base import RequestResponseEndpoint

from opsmesh.bootstrap.http_policy import gateway_identifier, gateway_policy
from opsmesh.bootstrap.mail import InvitationMailAuthorization, InvitationMailRegistrar
from opsmesh.bootstrap.request_context import RequestContextMiddleware
from opsmesh.bootstrap.resources import (
    DatabaseResources,
    build_api_resources,
    build_database_resources,
    build_json_cache,
)
from opsmesh.bootstrap.routers import register_routers
from opsmesh.bootstrap.telemetry import configure_api_telemetry, flush_process_telemetry
from opsmesh.config import Settings, get_settings
from opsmesh.governance.approvals.targets import NoApprovalTargetNotifier
from opsmesh.governance.security_events.service import SecurityEventLog
from opsmesh.identity.auth.contracts import RejectingDelegationResolver
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.provisioning import (
    AccountDefaults,
    InvitationAccountService,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
from opsmesh.shared.http.errors import ErrorResponse, register_error_handlers
from opsmesh.shared.http.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from opsmesh.shared.redis.client import close_redis_client
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.shared.version import package_version
from opsmesh.telemetry.logging import configure_logging, shutdown_logging

ERROR_RESPONSE_STATUS_CODES = (400, 401, 403, 404, 409, 422)


def create_event_loop() -> asyncio.AbstractEventLoop:
    """Uvicorn loop factory compatible with psycopg async connections on Windows."""
    return asyncio.SelectorEventLoop()


def assemble_feature_services(app: FastAPI, settings: Settings, cipher: SecretCipher) -> None:
    """Assemble the identity and mail services the routes resolve per request.

    The services are stateless facades over the injected owners: bootstrap
    decides the defaults, the delegation policy and the mail source admission,
    and the feature modules keep their own transactions.
    """
    defaults = AccountDefaults(
        token_limit=settings.default_user_token_limit,
        storage_limit_bytes=settings.default_user_storage_limit_bytes,
    )
    auth_service = AuthService(
        account_opening=ProvisionedAccountOpening(defaults),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityEventLog(),
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    app.state.settings = settings
    app.state.auth_service = auth_service
    # No delegation producer exists before the extensions step: every delegated
    # call is rejected until one is registered.
    app.state.delegation_resolver = RejectingDelegationResolver()
    app.state.approval_notifier = NoApprovalTargetNotifier()
    app.state.invitation_account_opening = InvitationAccountService(auth_service)
    app.state.invitation_mail_sender = InvitationMailRegistrar(
        authorization=InvitationMailAuthorization(),
        cipher=cipher,
        daily_source_limit=settings.mail_daily_source_limit,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Assemble the application; without settings the process configuration is read."""
    app_settings = settings if settings is not None else get_settings()
    # Unified logging comes first: it resets the root handlers, so the OTLP log
    # handler installed by the telemetry setup below survives and every log
    # record reaches both the bounded stdout queue and the log exporter.
    configure_logging(
        level=app_settings.log_level,
        format=app_settings.log_format,
        queue_capacity=app_settings.log_queue_capacity,
        service_name=app_settings.service_name,
        environment=app_settings.environment,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database: DatabaseResources | None = None
        redis_client: Redis[str] | None = None
        try:
            database = build_database_resources(app_settings)
            app.state.database_engine = database.engine
            app.state.database_sessions = database.sessions
            app.state.secret_cipher = database.cipher
            app.state.storage_root = app_settings.storage_root
            app.state.trusted_proxy_hops = app_settings.trusted_proxy_hops
            app.state.trusted_proxy_networks = app_settings.trusted_proxy_networks
            assemble_feature_services(app, app_settings, database.cipher)
            try:
                api_resources = build_api_resources(app_settings)
                redis_client = api_resources.redis_client
                app.state.redis_client = redis_client
                app.state.rate_limiter = api_resources.rate_limiter
                app.state.json_cache = build_json_cache(
                    redis_client, key_prefix=app_settings.redis_key_prefix
                )
                yield
            finally:
                # Reverse acquisition order: release later resources before earlier ones.
                app.state.redis_client = None
                app.state.rate_limiter = None
                app.state.json_cache = None
                if redis_client is not None:
                    close_redis_client(redis_client)
                app.state.settings = None
                app.state.auth_service = None
                app.state.delegation_resolver = None
                app.state.approval_notifier = None
                app.state.invitation_account_opening = None
                app.state.invitation_mail_sender = None
                if database is not None:
                    database.engine.dispose()
        finally:
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
    app.state.json_cache = None
    app.state.settings = None
    app.state.auth_service = None
    app.state.delegation_resolver = None
    app.state.approval_notifier = None
    app.state.invitation_account_opening = None
    app.state.invitation_mail_sender = None
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
