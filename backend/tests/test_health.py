from fastapi import Query
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


def test_health_endpoint_returns_service_status() -> None:
    app = create_app(Settings(environment="test", log_format="text"))
    client = TestClient(app)

    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"
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


def test_http_errors_use_consistent_error_envelope() -> None:
    app = create_app(Settings(environment="test", log_format="text", internal_api_token="token"))
    client = TestClient(app)

    response = client.get("/api/v1/workspaces")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["message"] == "Invalid or missing authorization token"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


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
