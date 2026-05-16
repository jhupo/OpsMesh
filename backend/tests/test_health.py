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

