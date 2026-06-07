from datetime import UTC, datetime, timedelta

import fakeredis
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings
from backend.app.core.errors import QuotaExceededError
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.main import create_app
from backend.app.operations.models import WorkerNode
from backend.app.redis.dependencies import get_redis_client


def test_health_endpoint_returns_service_status() -> None:
    app = create_app(Settings(environment="test", log_format="text"))
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
    assert response.headers["traceparent"].startswith(
        "00-0123456789abcdef0123456789abcdef-"
    )
    assert int(response.headers["X-Process-Time-Ms"]) >= 0
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.json() == {
        "status": "ok",
        "service": "chaincloud-backend",
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
            storage_root=".chaincloud-test-storage",
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
            cors_origins=["https://console.chaincloud.example"],
        )
    )
    client = TestClient(app)

    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://console.chaincloud.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://console.chaincloud.example"


def test_readiness_endpoint_checks_dependencies() -> None:
    app, session = _health_client_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root=".chaincloud-test-storage",
        )
    )
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {
        "database": "ok",
        "redis": "ok",
        "storage": "ok",
        "worker_queue": "ok",
        "workers_online": "disabled",
    }
    session.close()


def test_readiness_endpoint_can_require_recent_online_worker() -> None:
    app, session = _health_client_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root=".chaincloud-test-storage",
            readiness_worker_check_enabled=True,
            readiness_worker_stale_after_seconds=120,
        )
    )
    session.add(
        WorkerNode(
            worker_id="worker-ready",
            worker_type="cloud",
            status="online",
            queue_name="agent_runs",
            capacity={"max_jobs": 2},
            details={},
            last_seen_at=datetime.now(UTC) - timedelta(seconds=30),
        )
    )
    session.commit()
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"]["workers_online"] == "ok"
    session.close()


def test_readiness_endpoint_returns_503_when_required_workers_are_stale() -> None:
    app, session = _health_client_app(
        Settings(
            environment="test",
            log_format="text",
            storage_root=".chaincloud-test-storage",
            readiness_worker_check_enabled=True,
            readiness_worker_stale_after_seconds=120,
        )
    )
    session.add(
        WorkerNode(
            worker_id="worker-stale",
            worker_type="cloud",
            status="online",
            queue_name="agent_runs",
            capacity={"max_jobs": 2},
            details={},
            last_seen_at=datetime.now(UTC) - timedelta(seconds=300),
        )
    )
    session.commit()
    client = TestClient(app)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["details"]["dependencies"]["workers_online"] == "stale"
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
            storage_root="backend/tests/test_config.py",
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
        raise QuotaExceededError(
            "Runtime quota exceeded",
            details={"quota_key": "memory_mb"},
        )

    client = TestClient(app)

    response = client.get("/domain-error-demo")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quota_exceeded"
    assert response.json()["error"]["message"] == "Runtime quota exceeded"
    assert response.json()["error"]["details"] == {"quota_key": "memory_mb"}
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


class BrokenRedis:
    def ping(self) -> bool:
        raise ConnectionError("redis unavailable")


def _health_client_app(
    settings: Settings | None = None,
    redis: object | None = None,
) -> tuple[FastAPI, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    app = create_app(settings or Settings(environment="test", log_format="text"))

    def override_db_session() -> object:
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_redis_client] = lambda: redis or fakeredis.FakeRedis()
    return app, session


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
