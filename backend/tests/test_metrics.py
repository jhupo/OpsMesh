from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.metrics import MetricsRegistry, metrics_registry
from backend.app.main import create_app


def test_metrics_registry_renders_counters_and_histograms() -> None:
    registry = MetricsRegistry()

    registry.increment("demo_total", labels={"path": "/x", "status": 200})
    registry.observe("demo_duration_ms", 42, buckets=(10, 50), labels={"path": "/x"})

    rendered = registry.render_prometheus()

    assert 'demo_total{path="/x",status="200"} 1' in rendered
    assert 'demo_duration_ms_bucket{le="50",path="/x"} 1' in rendered
    assert 'demo_duration_ms_bucket{le="+Inf",path="/x"} 1' in rendered


def test_metrics_endpoint_exposes_http_request_metrics() -> None:
    metrics_registry.clear()
    app = create_app(Settings(environment="test", log_format="text"))
    client = TestClient(app)

    health = client.get("/api/v1/health")
    metrics = client.get("/api/v1/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
    body = metrics.text
    assert (
        'chaincloud_http_requests_total{method="GET",path="/api/v1/health",status="200"} 1'
        in body
    )
    assert 'chaincloud_http_request_duration_ms_bucket{' in body
    assert 'path="/api/v1/health"' in body
