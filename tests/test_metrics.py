from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from opsmesh.governance.audit.models import AuditIntegrityCheck
from opsmesh.governance.costs.models import (
    ModelPricingRule,
    ModelUsageRecord,
    WorkspaceCostBudget,
)
from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.operations.metrics.temporal import TemporalPrometheusMetrics
from opsmesh.runtime.spaces.models import RuntimeSpace, RuntimeSpaceQuota
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.middleware import _metrics_path
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.shared.telemetry.metrics import GaugeMetric, MetricsRegistry, metrics_registry
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import product_engine


def test_metrics_registry_renders_counters_and_histograms() -> None:
    registry = MetricsRegistry()

    registry.increment("demo_total", labels={"path": "/x", "status": 200})
    registry.observe("demo_duration_ms", 42, buckets=(10, 50), labels={"path": "/x"})

    rendered = registry.render_prometheus(
        gauges=(GaugeMetric("demo_workers", 2, labels={"state": "online"}),)
    )

    assert "# TYPE demo_total counter" in rendered
    assert "# TYPE demo_workers gauge" in rendered
    assert "# TYPE demo_duration_ms histogram" in rendered
    assert 'demo_total{path="/x",status="200"} 1' in rendered
    assert 'demo_workers{state="online"} 2' in rendered
    assert 'demo_duration_ms_bucket{le="50.0",path="/x"} 1' in rendered
    assert 'demo_duration_ms_bucket{le="+Inf",path="/x"} 1' in rendered
    assert 'demo_duration_ms_count{path="/x"} 1' in rendered
    assert 'demo_duration_ms_sum{path="/x"} 42' in rendered


def test_metrics_endpoint_exposes_http_request_metrics() -> None:
    metrics_registry.clear()
    client, _ = _client(fakeredis.FakeRedis(decode_responses=True))

    health = client.get("/api/v1/health")
    metrics = client.get("/api/v1/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
    body = metrics.text
    assert 'opsmesh_http_requests_total{method="GET",path="/api/v1/health",status="200"} 1' in body
    assert "opsmesh_http_request_duration_ms_bucket{" in body
    assert "opsmesh_http_request_duration_ms_count{" in body
    assert "opsmesh_http_request_duration_ms_sum{" in body
    assert 'path="/api/v1/health"' in body


def test_metrics_endpoint_exposes_operations_gauges_without_high_cardinality_labels() -> None:
    metrics_registry.clear()
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    workspace = _seed_metrics_fixture(session, redis)
    assert client.get("/api/v1/health").status_code == 200
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    body = response.text
    assert 'opsmesh_temporal_dispatches{state="pending"} 1' in body
    assert 'opsmesh_metrics_collection_success{source="postgres"} 1' in body
    assert 'opsmesh_sandbox_environments{sandbox_backend="docker"} 1' in body
    assert 'opsmesh_runtime_active_runs{sandbox_backend="docker"} 1' in body
    assert 'opsmesh_runtime_space_quota_reserved{quota_key="storage_mb",unit="mb"} 80' in body
    assert 'opsmesh_runtime_space_quota_limit{quota_key="storage_mb",unit="mb"} 100' in body
    assert 'opsmesh_runtime_space_quota_usage_ratio{quota_key="storage_mb",unit="mb"} 0.8' in body
    assert 'opsmesh_team_runtimes{health="healthy"} 1' in body
    assert 'opsmesh_team_runtimes{health="paused"} 1' in body
    assert str(workspace.id) not in body


def test_metrics_endpoint_marks_postgres_collection_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics_registry.clear()
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, _ = _client(redis)

    def fail_worker_gauges(*args: object, **kwargs: object) -> object:
        raise SQLAlchemyError("postgres unavailable")

    monkeypatch.setattr(TemporalPrometheusMetrics, "gauges", fail_worker_gauges)

    response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    assert 'opsmesh_metrics_collection_success{source="postgres"} 0' in response.text


def test_temporal_metrics_report_committed_publication_pressure_without_payloads() -> None:
    client, session = _client(fakeredis.FakeRedis(decode_responses=True))
    _seed_metrics_fixture(session, fakeredis.FakeRedis(decode_responses=True))
    dispatch = session.query(TemporalOutboxEntry).one()
    dispatch.created_at = datetime.now(UTC) - timedelta(minutes=20)
    dispatch.last_error_code = "TemporalUnavailable"
    dispatch.payload = {**dispatch.payload, "private_canary": "payload-must-stay-private"}
    session.commit()

    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert "opsmesh_temporal_dispatch_publication_errors 1" in response.text
    oldest = next(
        line
        for line in response.text.splitlines()
        if line.startswith("opsmesh_temporal_oldest_pending_dispatch_age_seconds ")
    )
    assert 1200 <= float(oldest.split()[-1]) < 1260
    assert "payload-must-stay-private" not in response.text
    assert str(dispatch.workflow_id) not in response.text

    dispatch.published_at = datetime.now(UTC)
    session.commit()
    settled = client.get("/api/v1/metrics").text
    assert "opsmesh_temporal_dispatch_publication_errors 0" in settled
    assert "opsmesh_temporal_oldest_pending_dispatch_age_seconds 0" in settled


def test_metrics_endpoint_exposes_audit_and_cost_governance_without_tenant_labels() -> None:
    metrics_registry.clear()
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    workspace = _seed_metrics_fixture(session, redis)
    run = session.query(AgentRun).filter(AgentRun.workspace_id == workspace.id).first()
    assert run is not None
    now = datetime.now(UTC)
    pricing = ModelPricingRule(
        workspace_id=workspace.id,
        provider="openai",
        model="gpt-cost",
        version="metrics-v1",
        currency="USD",
        input_rate_per_million=Decimal("0"),
        output_rate_per_million=Decimal("0"),
        cached_input_rate_per_million=Decimal("0"),
        request_rate=Decimal("1.25"),
        effective_from=now - timedelta(days=1),
        status="active",
        source="test",
    )
    session.add(pricing)
    session.flush()
    session.add_all(
        [
            AuditIntegrityCheck(
                workspace_id=workspace.id,
                checked_events=3,
                valid=True,
                created_at=now,
            ),
            ModelUsageRecord(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                provider="openai",
                model="gpt-cost",
                pricing_rule_id=pricing.id,
                pricing_version=pricing.version,
                metering_status="priced",
                job_attempt=0,
                request_count=1,
                input_tokens=1_000,
                output_tokens=500,
                cached_input_tokens=0,
                reasoning_tokens=0,
                total_tokens=1_500,
                currency="USD",
                input_cost=Decimal("0"),
                output_cost=Decimal("0"),
                cached_input_cost=Decimal("0"),
                request_cost=Decimal("1.25"),
                total_cost=Decimal("1.25"),
                raw_usage={},
                occurred_at=now,
            ),
            WorkspaceCostBudget(
                workspace_id=workspace.id,
                currency="USD",
                monthly_limit=Decimal("2"),
                warning_ratio=Decimal("0.5"),
                enforcement="warn",
                enabled=True,
            ),
        ]
    )
    session.commit()

    response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    body = response.text
    assert 'opsmesh_audit_integrity_workspaces{state="valid"} 1' in body
    assert 'opsmesh_audit_integrity_workspaces{state="missing"} 0' in body
    assert 'opsmesh_model_usage_records_24h{state="priced"} 1' in body
    assert 'opsmesh_cost_budget_workspaces{state="warning"} 1' in body
    assert 'opsmesh_model_cost_current_month{currency="USD"} 1.25' in body
    assert str(workspace.id) not in body


def test_metrics_path_uses_route_template_to_avoid_high_cardinality_labels() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/workspaces/123/files/456/download",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
            "route": SimpleNamespace(
                path="/api/v1/workspaces/{workspace_id}/files/{file_id}/download",
            ),
        }
    )

    assert _metrics_path(request) == ("/api/v1/workspaces/{workspace_id}/files/{file_id}/download")


def test_unmatched_routes_share_a_bounded_metrics_label() -> None:
    metrics_registry.clear()
    client, _ = _client(fakeredis.FakeRedis(decode_responses=True))

    assert client.get("/random-not-found-a").status_code == 404
    assert client.get("/random-not-found-b").status_code == 404
    body = client.get("/api/v1/metrics").text

    assert 'opsmesh_http_requests_total{method="GET",path="/__unmatched__",status="404"} 2' in body
    assert "/random-not-found-a" not in body
    assert "/random-not-found-b" not in body


def test_unknown_http_methods_share_a_bounded_metrics_label() -> None:
    metrics_registry.clear()
    client, _ = _client(fakeredis.FakeRedis(decode_responses=True))

    assert client.request("RANDOM-METHOD-A", "/api/v1/health").status_code == 405
    assert client.request("RANDOM-METHOD-B", "/api/v1/health").status_code == 405
    body = client.get("/api/v1/metrics").text

    assert (
        'opsmesh_http_requests_total{method="OTHER",path="/api/v1/health",status="405"} 2' in body
    )
    assert "RANDOM-METHOD-A" not in body
    assert "RANDOM-METHOD-B" not in body


def _client(redis: fakeredis.FakeRedis) -> tuple[TestClient, Session]:
    engine = product_engine()
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_session = session_factory()
    app = create_app(Settings(environment="test", log_format="text"))

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), seed_session


def _seed_metrics_fixture(session: Session, redis: fakeredis.FakeRedis) -> Workspace:
    user = User(email="metrics@example.com", display_name="Metrics")
    workspace = Workspace(owner=user, name="Metrics", slug="metrics", settings={})
    session.add_all([user, workspace])
    session.flush()
    space = RuntimeSpace(workspace_id=workspace.id, name="Metrics Space", scope="workspace")
    session.add(space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=space.id,
        name="metrics-runtime",
        sandbox_backend_id="docker",
        sandbox_options={"image": "runtime:test"},
    )
    session.add(runtime)
    session.flush()
    stage_execution(
        session,
        ExecutionRequest(
            workspace_id=workspace.id,
            resource_id=runtime.id,
            kind=ExecutionKind.SANDBOX_CLEANUP,
        ),
    )
    session.add_all(
        [
            AgentTeam(
                workspace_id=workspace.id,
                name="Healthy Team",
                team_type="software",
                default_task_policy={
                    "team_runtime": {"status": "running", "workspace_runtime_id": str(runtime.id)}
                },
            ),
            AgentTeam(
                workspace_id=workspace.id,
                name="Paused Team",
                team_type="software",
                default_task_policy={"team_runtime": {"status": "paused"}},
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=space.id,
                quota_key="storage_mb",
                limit_value=100,
                reserved_value=80,
                unit="mb",
                status="active",
            ),
            AgentRun(workspace_id=workspace.id, runtime_id=runtime.id, status="running"),
        ]
    )
    session.commit()
    return workspace
