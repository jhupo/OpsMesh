"""FastAPI assembly with process lifecycle and reverse-order resource release."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from functools import partial
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint

from opsmesh.bootstrap.accounts import AccountResources
from opsmesh.bootstrap.capabilities import (
    assemble_capability_validation,
    assemble_storage_quota_ledger,
)
from opsmesh.bootstrap.connections import (
    build_api_resources,
    build_database_resources,
    build_json_cache,
)
from opsmesh.bootstrap.extensions import (
    ExtensionModelAuthorization,
    ExtensionReconciliation,
    ExtensionReferences,
    ExtensionScopes,
    RunExtensions,
    installed_images,
)
from opsmesh.bootstrap.http_policy import gateway_identifier, gateway_policy
from opsmesh.bootstrap.metrics import product_metrics
from opsmesh.bootstrap.orchestration import (
    ConfigurationCleanupAuthorization,
    ConfigurationDirectories,
    ConfigurationRunSources,
    ExecutionAssets,
    ExecutionModelConnections,
    execution_bindings,
    wake_user_runs,
)
from opsmesh.bootstrap.request_context import RequestContextMiddleware
from opsmesh.bootstrap.routers import (
    ActiveRunActivity,
    ConfigurationAuditScopeAdmission,
    ConfigurationExecutionScopeAdmission,
    ConfigurationScopeAdmission,
    register_routers,
)
from opsmesh.bootstrap.telemetry import configure_api_telemetry, flush_process_telemetry
from opsmesh.config import Settings, get_settings
from opsmesh.extensions.delegation import InvocationDelegation
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.provisioning import (
    AccountDefaults,
    InvitationAccountService,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
from opsmesh.identity.quotas import service as quotas
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider
from opsmesh.orchestration.runs.model_operations import ModelOperations
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.platform.audit.service import SecurityAuditRecorder
from opsmesh.platform.policies.redaction import PLATFORM_REDACTION
from opsmesh.resources.artifacts.service import RejectingArtifactAuthorization
from opsmesh.shared.http.errors import ErrorResponse, register_error_handlers
from opsmesh.shared.http.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from opsmesh.shared.redis.client import close_redis_client
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.version import package_version
from opsmesh.telemetry.logging import configure_logging, shutdown_logging
from opsmesh.telemetry.metrics import start_metrics_server

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
    from opsmesh.bootstrap.health import temporal_health_probe
    from opsmesh.bootstrap.orchestration import FlowOperationLimit
    from opsmesh.bootstrap.platform import active_work, operation_handlers
    from opsmesh.orchestration.runs.activities import FlowActivities
    from opsmesh.platform.operations.admission import is_open

    defaults = AccountDefaults(
        token_limit=settings.default_user_token_limit,
        storage_limit_bytes=settings.default_user_storage_limit_bytes,
    )
    auth_service = AuthService(
        account_opening=ProvisionedAccountOpening(defaults, AccountResources(settings)),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityAuditRecorder(),
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    app.state.settings = settings
    assemble_capability_validation()
    app.state.model_key_cipher = cipher
    app.state.configuration_resources = ConfigurationDirectories()
    app.state.temporal_health_probe = temporal_health_probe(settings)
    app.state.platform_operation_handlers = operation_handlers(cipher)
    app.state.platform_active_work = active_work
    app.state.channel_admission = is_open
    app.state.auth_service = auth_service
    app.state.extension_scopes = ExtensionScopes(ConfigurationExecutionScopeAdmission(), cipher)
    app.state.extension_reconciliation = ExtensionReconciliation(cipher)
    app.state.extension_references = ExtensionReferences(ConfigurationExecutionScopeAdmission())
    app.state.delegation_resolver = InvocationDelegation(
        UserIdentityDirectory(),
        app.state.extension_scopes,
    )
    app.state.invitation_account_opening = InvitationAccountService(auth_service)
    # The storage ledger has its identity owner; the workspace step registers
    # the real resource and audit scope admissions, resolved through the one
    # space and project access paths. The artifact record authorization keeps
    # its rejecting default until the execution step registers a real owner.
    app.state.storage_quota_ledger = assemble_storage_quota_ledger()
    app.state.scope_admission = ConfigurationScopeAdmission()
    app.state.audit_scope_admission = ConfigurationAuditScopeAdmission()
    app.state.artifact_authorization = RejectingArtifactAuthorization()
    # The execution step owns its scope admission and run guard: a model
    # session is authorized against the space and project access paths,
    # and the run guard reads the run table the session modules never
    # import. The native history gateway follows with the lifespan, once
    # its engine exists.
    app.state.execution_scope_admission = ConfigurationExecutionScopeAdmission()
    app.state.session_run_activity = ActiveRunActivity()
    app.state.execution_bindings = execution_bindings()
    app.state.execution_bindings = replace(
        app.state.execution_bindings,
        extensions=RunExtensions(app.state.extension_scopes, cipher),
        sources=ConfigurationRunSources(
            cipher, app.state.execution_scope_admission, app.state.extension_scopes
        ),
        cleanup=ConfigurationCleanupAuthorization(),
        limit_operation=FlowOperationLimit(cipher),
    )
    from pathlib import Path

    app.state.execution_assets = ExecutionAssets(
        Path(settings.storage_root), ConfigurationScopeAdmission()
    )
    app.state.execution_storage = quotas
    app.state.capacity_change_notifier = wake_user_runs
    app.state.execution_sandbox_images = tuple(settings.execution_sandbox_images)
    app.state.installed_sandbox_images = installed_images
    if getattr(app.state, "database_sessions", None) is not None:
        app.state.flow_activities = FlowActivities(
            app.state.database_sessions,
            UserIdentityDirectory(),
            app.state.extension_scopes,
            app.state.execution_scope_admission,
            cipher,
            app.state.execution_assets,
            app.state.execution_bindings,
        )

        def start_channel_flow(
            session: Session,
            *,
            actor: CurrentUser,
            revision_id: UUID,
            configuration_id: UUID,
            input: dict[str, object],
            idempotency_key: str,
        ) -> UUID:
            from opsmesh.orchestration.runs.schemas import FlowRunCreate
            from opsmesh.orchestration.runs.service import start_run

            run = start_run(
                session,
                actor=actor,
                request=FlowRunCreate(
                    revision_id=revision_id, configuration_id=configuration_id, input=input
                ),
                idempotency_key=idempotency_key,
                cipher=cipher,
            )
            app.state.flow_activities.validate_start(session, actor=actor, run=run)
            return run.id

        app.state.channel_flow_starter = start_channel_flow
        app.state.flow_definition_validator = app.state.flow_activities.validate_definition
        app.state.execution_model_operations = ModelOperations(
            ExecutionModelProvider(
                app.state.database_sessions,
                app.state.execution_bindings,
                app.state.execution_scope_admission,
                ExecutionModelConnections(cipher, settings.execution_model_context_windows),
                cipher,
            ),
            ExtensionModelAuthorization(app.state.extension_scopes),
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
                history_gateway = HistoryGateway(database.sessions)
                app.state.history_gateway = history_gateway
                api_resources = build_api_resources(app_settings)
                redis_client = api_resources.redis_client
                resources.callback(close_redis_client, redis_client)
                app.state.redis_client = redis_client
                app.state.rate_limiter = api_resources.rate_limiter
                app.state.json_cache = build_json_cache(
                    redis_client, key_prefix=app_settings.redis_key_prefix
                )
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
                "json_cache",
                "storage_quota_ledger",
                "scope_admission",
                "audit_scope_admission",
                "channel_admission",
                "artifact_authorization",
                "settings",
                "auth_service",
                "delegation_resolver",
                "invitation_account_opening",
                "history_gateway",
                "metrics_server",
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
    app.state.json_cache = None
    app.state.history_gateway = None
    app.state.storage_quota_ledger = None
    app.state.scope_admission = None
    app.state.audit_scope_admission = None
    app.state.artifact_authorization = None
    app.state.settings = None
    app.state.auth_service = None
    app.state.delegation_resolver = None
    app.state.invitation_account_opening = None
    app.state.channel_flow_starter = None
    app.state.channel_admission = None
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
