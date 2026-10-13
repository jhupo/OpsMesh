import os
import subprocess
import sys

import fakeredis
import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.main import create_app
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.redis.dependencies import get_redis_client
from tests.fixtures.database import product_engine


@pytest.fixture(autouse=True)
def temporal_readiness(monkeypatch):
    async def available(request):
        return "ok"

    monkeypatch.setattr("opsmesh.platform.health.routes._check_temporal", available)


def test_health_endpoint_returns_service_status() -> None:
    # Native OTel providers cannot be restarted after another app shuts them down.
    if os.getenv("OPSMESH_ISOLATED_HEALTH_TRACE") != "1":
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                f"{__file__}::test_health_endpoint_returns_service_status",
            ],
            env={**os.environ, "OPSMESH_ISOLATED_HEALTH_TRACE": "1"},
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return
    app = create_app(Settings(environment="test", log_format="text", otel_excluded_urls=""))
    client = TestClient(app)

    response = client.get(
        "/api/v1/health",
        headers={
            "X-Request-ID": "test-request-id",
            "traceparent": "00-0123456789abcdef0123456789abcdef-abcdef0123456789-01",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"
    assert response.headers["X-Trace-ID"] == "0123456789abcdef0123456789abcdef"
    assert response.headers["X-Parent-Span-ID"] == "abcdef0123456789"
    assert response.headers["traceparent"].startswith("00-0123456789abcdef0123456789abcdef-")
    assert int(response.headers["X-Process-Time-Ms"]) >= 0
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.json() == {
        "status": "ok",
        "service": "opsmesh-backend",
        "environment": "test",
        "request_id": "test-request-id",
    }


def test_health_endpoint_generates_request_id_when_missing() -> None:
    app = create_app(Settings(environment="test", log_format="text"))
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_liveness_and_startup_endpoints_return_split_health_status() -> None:
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root=".opsmesh-test-storage",
        )
    )
    client = TestClient(app)

    live = client.get("/api/v1/health/live")
    startup = client.get("/api/v1/health/startup")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert startup.status_code == 200
    assert startup.json()["dependencies"] == {
        "configuration": "ok",
        "storage": "ok",
        "temporal": "ok",
    }


def test_app_lifespan_closes_owned_redis_client() -> None:
    app = create_app(Settings(environment="test", log_format="text"))

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert app.state.redis_client.connection_pool._available_connections == []  # noqa: SLF001


def test_cors_middleware_uses_configured_origins() -> None:
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            cors_origins=["https://console.opsmesh.example"],
        )
    )
    client = TestClient(app)

    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://console.opsmesh.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://console.opsmesh.example"


def test_readiness_endpoint_checks_dependencies() -> None:
    app, session = _health_client_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root=".opsmesh-test-storage",
        )
    )
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {
        "database": "ok",
        "redis": "ok",
        "storage": "ok",
        "temporal": "ok",
    }
    session.close()


def test_readiness_endpoint_returns_503_when_dependency_fails() -> None:
    app, session = _health_client_app(redis=BrokenRedis())
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    session.close()


def test_readiness_endpoint_returns_503_when_storage_is_unavailable() -> None:
    app, session = _health_client_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root="tests/test_config.py",
        )
    )
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    session.close()


def test_http_errors_use_consistent_error_envelope() -> None:
    app, session = _health_client_app(
        Settings(environment="test", log_format="text", internal_api_token="old,new")
    )
    client = TestClient(app)

    response = client.get("/api/v1/workspaces")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["message"] == "Invalid or missing authorization token"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]

    rotated = client.get("/api/v1/workspaces", headers={"Authorization": "Bearer new"})
    assert rotated.status_code == 422
    session.close()


def test_validation_errors_use_consistent_error_envelope() -> None:
    app = create_app(Settings(environment="test", log_format="text", internal_api_token="token"))

    @app.get("/validation-demo")
    async def validation_demo(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    client = TestClient(app)

    response = client.get("/validation-demo?limit=0")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert body["error"]["details"]


def test_domain_errors_use_consistent_error_envelope() -> None:
    app = create_app(Settings(environment="test", log_format="text"))

    @app.get("/domain-error-demo")
    async def domain_error_demo() -> None:
        raise ConflictError(
            "Resource update conflict",
            details={"resource": "test"},
        )

    client = TestClient(app)

    response = client.get("/domain-error-demo")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["message"] == "Resource update conflict"
    assert response.json()["error"]["details"] == {"resource": "test"}
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


class BrokenRedis:
    def ping(self) -> bool:
        raise ConnectionError("redis unavailable")


def _health_client_app(
    settings: Settings | None = None,
    redis: object | None = None,
) -> tuple[FastAPI, Session]:
    engine = product_engine()
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    app = create_app(settings or Settings(environment="test", log_format="text"))

    def override_db_session() -> object:
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_redis_client] = lambda: redis or fakeredis.FakeRedis()
    return app, session
