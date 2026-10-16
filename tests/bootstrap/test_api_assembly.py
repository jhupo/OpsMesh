"""S2 bootstrap assembly tests: lifecycle release, gateway wiring and error contract."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.handlers import QueueHandler
from typing import cast

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

import opsmesh.bootstrap.api as api_module
import opsmesh.bootstrap.resources as resources_module
import opsmesh.bootstrap.telemetry as telemetry_module
from opsmesh.bootstrap.http_policy import gateway_policy
from opsmesh.bootstrap.resources import APIResources, DatabaseResources
from opsmesh.config import Settings
from opsmesh.shared.db.session import database_pool_snapshot
from opsmesh.shared.http.errors import register_error_handlers
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.security.secrets import SecretCipher

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://opsmesh:secret@db.invalid:5432/opsmesh",
        storage_root=".opsmesh-test-storage",
        **overrides,  # type: ignore[arg-type]
    )


def fake_database_resources(app_settings: Settings, engine: FakeEngine) -> DatabaseResources:
    return DatabaseResources(
        engine=cast("Engine", engine),
        sessions=sessionmaker(cast("Engine", engine), expire_on_commit=False),
        cipher=SecretCipher(key=TEST_KEY, key_id="test"),
    )


def fake_api_resources(app_settings: Settings) -> APIResources:
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    return APIResources(
        redis_client=cast("object", redis_client),
        rate_limiter=FixedWindowRateLimiter.from_redis(
            redis_client, key_prefix=app_settings.redis_key_prefix
        ),
    )


def _noop_export(*args: object, **kwargs: object) -> None:
    """Skip real OTLP exporters while keeping the handler wiring observable."""


def install_fake_assembly(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """Replace the infrastructure builders with observable fakes."""
    engine = FakeEngine()
    monkeypatch.setattr(
        api_module,
        "build_database_resources",
        lambda app_settings: fake_database_resources(app_settings, engine),
    )
    monkeypatch.setattr(api_module, "build_api_resources", fake_api_resources)
    return engine


def run_lifespan(app: FastAPI) -> None:
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def run() -> AsyncIterator[None]:
        async with lifespan(app):
            yield

    asyncio.run(run().__aenter__())


def test_startup_failure_disposes_engine_before_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = install_fake_assembly(monkeypatch)

    def failing_resources(app_settings: Settings) -> APIResources:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(api_module, "build_api_resources", failing_resources)

    app = api_module.create_app(settings())
    with pytest.raises(RuntimeError, match="redis unavailable"):
        run_lifespan(app)
    assert engine.disposed, "the database engine must be released on startup failure"


def test_database_startup_failure_stops_the_logging_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logging is configured before any other resource; its listener must be
    stopped even when the next acquisition fails."""
    install_fake_assembly(monkeypatch)

    def failing_database(app_settings: Settings) -> DatabaseResources:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api_module, "build_database_resources", failing_database)
    shutdowns: list[str] = []
    real_shutdown = api_module.shutdown_logging

    def spy() -> None:
        shutdowns.append("stopped")
        real_shutdown()

    monkeypatch.setattr(api_module, "shutdown_logging", spy)
    app = api_module.create_app(settings())
    with pytest.raises(RuntimeError, match="database unavailable"):
        run_lifespan(app)
    assert shutdowns, "the logging listener must be stopped when startup fails after logging"


def test_application_lifespan_sets_state_and_releases_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = install_fake_assembly(monkeypatch)
    app = api_module.create_app(settings())
    lifespan = app.router.lifespan_context

    async def run() -> None:
        async with lifespan(app):
            assert app.state.storage_root == ".opsmesh-test-storage"
            assert app.state.database_sessions is not None
            assert app.state.database_engine is not None
            assert app.state.rate_limiter is not None
            assert app.state.json_cache is not None

    asyncio.run(run())
    assert engine.disposed, "the database engine must be released on shutdown"


def test_application_assembles_without_explicit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ASGI factory used by uvicorn and the container entrypoints takes no argument."""
    install_fake_assembly(monkeypatch)
    app = api_module.create_app()
    assert app.title == "OpsMesh"


def test_api_process_configures_tracing_and_unified_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_assembly(monkeypatch)
    app = api_module.create_app(settings())
    provider = trace.get_tracer_provider()
    assert not isinstance(provider, trace.ProxyTracerProvider), (
        "the API process must install a real tracer provider"
    )
    with TestClient(app) as client:
        root_handlers = logging.getLogger().handlers
        assert any(isinstance(handler, QueueHandler) for handler in root_handlers), (
            "the API process must route logs through the unified bounded queue"
        )
        assert client.get("/api/v1/ping").status_code == 404


def test_api_log_export_handler_survives_the_unified_logging_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unified logging resets the root handlers, so it must run before the OTLP
    log handler is mounted; otherwise stdout receives logs but export gets none."""
    monkeypatch.setattr(telemetry_module, "configure_trace_export", _noop_export)
    monkeypatch.setattr(telemetry_module, "configure_log_export", _noop_export)
    install_fake_assembly(monkeypatch)
    api_module.create_app(settings(otel_exporter_otlp_endpoint="http://127.0.0.1:4317"))
    root_handlers = logging.getLogger().handlers
    assert any(isinstance(handler, QueueHandler) for handler in root_handlers), (
        "the API process must route logs through the unified bounded queue"
    )
    assert any(isinstance(handler, LoggingHandler) for handler in root_handlers), (
        "the OTLP log handler must stay mounted on the root logger after logging setup"
    )


def test_application_builds_its_database_through_the_configured_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Settings] = []
    complete_assembly = resources_module.build_database_resources

    def spy(app_settings: Settings) -> DatabaseResources:
        calls.append(app_settings)
        return complete_assembly(app_settings)

    monkeypatch.setattr(api_module, "build_database_resources", spy)
    monkeypatch.setattr(api_module, "build_api_resources", fake_api_resources)
    app = api_module.create_app(
        settings(database_pool_size=3, database_max_overflow=1, database_statement_timeout_ms=4321)
    )
    run_lifespan(app)
    assert calls, "the application must build its database through the configured assembly"
    snapshot = database_pool_snapshot(app.state.database_engine)
    assert snapshot.pool_size == 3, "the configured pool size must reach the engine"
    assert snapshot.max_overflow == 1, "the configured overflow must reach the engine"


def test_api_docs_are_disabled_by_configuration() -> None:
    app = api_module.create_app(settings(enable_api_docs=False))
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found", path


def test_api_docs_are_served_when_enabled() -> None:
    app = api_module.create_app(settings(enable_api_docs=True))
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_unmatched_route_returns_unified_error_envelope() -> None:
    app = api_module.create_app(settings())
    client = TestClient(app)
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]


def test_method_not_allowed_returns_unified_error_envelope() -> None:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    register_error_handlers(app)
    client = TestClient(app)
    response = client.post("/ping")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_cors_preflight_is_answered_for_configured_origins() -> None:
    app = api_module.create_app(settings(cors_origins=["https://app.example"]))
    client = TestClient(app)
    response = client.options(
        "/api/v1/anything",
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example"


def test_configured_rate_limit_rejects_with_unified_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_assembly(monkeypatch)
    app = api_module.create_app(
        settings(
            api_rate_limit_enabled=True,
            api_rate_limit_requests=1,
            api_rate_limit_window_seconds=60,
        )
    )
    with TestClient(app) as client:
        first = client.get("/api/v1/ping")
        assert first.status_code == 404
        assert first.headers["X-RateLimit-Remaining"] == "0"
        second = client.get("/api/v1/ping")
        assert second.status_code == 429
        payload = second.json()
        assert payload["error"]["code"] == "rate_limited"
        assert payload["error"]["request_id"] == second.headers["X-Request-ID"]
        assert second.headers["Retry-After"]


def test_gateway_policy_classifies_paths() -> None:
    policy_for_path = gateway_policy(
        settings(
            api_rate_limit_requests=600,
            auth_rate_limit_requests=20,
            admin_rate_limit_requests=120,
        )
    )
    assert policy_for_path("/api/v1/auth/login").name == "authentication"
    assert policy_for_path("/api/v1/auth/login").limit == 20
    assert policy_for_path("/api/v1/platform/backups").name == "platform"
    assert policy_for_path("/api/v1/platform/backups").limit == 120
    assert policy_for_path("/api/v1/workspaces").name == "api"
    assert policy_for_path("/api/v1/workspaces").limit == 600
    assert policy_for_path("/docs").name == "api"
    assert policy_for_path("/api/v1").name == "api"
    assert policy_for_path("/api/v1/auth/login").fail_closed is True
    assert policy_for_path("/api/v1/workspaces").fail_closed is False
