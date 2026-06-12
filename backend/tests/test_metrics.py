from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.app.core.config import Settings
from backend.app.core.metrics import MetricsRegistry, metrics_registry
from backend.app.core.middleware import _metrics_path
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workspaces.models import Workspace


def test_metrics_registry_renders_counters_and_histograms() -> None:
    registry = MetricsRegistry()

    registry.increment("demo_total", labels={"path": "/x", "status": 200})
    registry.set_gauge("demo_workers", 2, labels={"state": "online"})
    registry.observe("demo_duration_ms", 42, buckets=(10, 50), labels={"path": "/x"})

    rendered = registry.render_prometheus()

    assert "# TYPE demo_total counter" in rendered
    assert "# TYPE demo_workers gauge" in rendered
    assert "# TYPE demo_duration_ms histogram" in rendered
    assert 'demo_total{path="/x",status="200"} 1' in rendered
    assert 'demo_workers{state="online"} 2' in rendered
    assert 'demo_duration_ms_bucket{le="50",path="/x"} 1' in rendered
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
    assert (
        'opsmesh_http_requests_total{method="GET",path="/api/v1/health",status="200"} 1'
        in body
    )
    assert 'opsmesh_http_request_duration_ms_bucket{' in body
    assert 'opsmesh_http_request_duration_ms_count{' in body
    assert 'opsmesh_http_request_duration_ms_sum{' in body
    assert 'path="/api/v1/health"' in body


def test_metrics_endpoint_exposes_operations_gauges_without_high_cardinality_labels() -> None:
    metrics_registry.clear()
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    workspace = _seed_metrics_fixture(session, redis)

    health = client.get("/api/v1/health")
    metrics = client.get("/api/v1/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
    body = metrics.text
    assert (
        'opsmesh_http_requests_total{method="GET",path="/api/v1/health",status="200"} 1'
        in body
    )
    assert 'opsmesh_queue_jobs{queue_name="agent_runs",state="queued"} 1' in body
    assert 'opsmesh_queue_jobs{queue_name="agent_runs",state="dead_letter"} 1' in body
    assert 'opsmesh_queue_idempotency_keys{queue_name="agent_runs"} 1' in body
    assert 'opsmesh_queue_oldest_queued_age_seconds{queue_name="agent_runs"}' in body
    assert 'opsmesh_workers{state="online"} 1' in body
    assert 'opsmesh_workers{state="offline"} 1' in body
    assert 'opsmesh_workers{state="stale"} 1' in body
    assert 'opsmesh_worker_leases{status="running"} 1' in body
    assert 'opsmesh_worker_leases{status="failed"} 1' in body
    assert (
        'opsmesh_runtime_capacity_slots{provider="cloud_docker",runtime_type="docker"} 2'
        in body
    )
    assert (
        'opsmesh_runtime_active_runs{provider="cloud_docker",runtime_type="docker"} 1'
        in body
    )
    assert (
        'opsmesh_runtime_saturation_ratio{provider="cloud_docker",runtime_type="docker"} 0.5'
        in body
    )
    assert (
        'opsmesh_runtime_space_quota_reserved{quota_key="storage_mb",unit="mb"} 80'
        in body
    )
    assert (
        'opsmesh_runtime_space_quota_limit{quota_key="storage_mb",unit="mb"} 100'
        in body
    )
    assert (
        'opsmesh_runtime_space_quota_usage_ratio{quota_key="storage_mb",unit="mb"} 0.8'
        in body
    )
    assert 'opsmesh_team_runtimes{health="healthy"} 1' in body
    assert 'opsmesh_team_runtimes{health="stale"} 1' in body
    assert "opsmesh_team_runtime_iterations_total 7" in body
    assert 'opsmesh_team_runtime_scheduled_loops{state="enabled"} 2' in body
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

    assert _metrics_path(request) == (
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/download"
    )


def _client(redis: fakeredis.FakeRedis) -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    now = datetime.now(UTC)
    user = User(email="metrics@example.com", display_name="Metrics")
    workspace = Workspace(owner=user, name="Metrics", slug="metrics", settings={})
    session.add_all([user, workspace])
    session.flush()
    keys = RedisKeyBuilder("opsmesh")
    queued_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="metrics-queued",
        created_at=now - timedelta(seconds=120),
    )
    dead_letter_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="metrics-dead-letter",
        created_at=now - timedelta(seconds=300),
    )
    redis.rpush(keys.queue("agent_runs"), queued_job.model_dump_json())
    redis.rpush(keys.dead_letter_queue("agent_runs"), dead_letter_job.model_dump_json())
    redis.set(keys.idempotency_key(str(workspace.id), queued_job.idempotency_key), "1")

    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Metrics Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="metrics-runtime",
        runtime_provider="cloud_docker",
        runtime_type="docker",
        capabilities={"capacity_slots": 2},
        status="running",
        connection_status="online",
    )
    session.add(runtime)
    session.flush()
    session.add_all(
        [
            AgentTeam(
                workspace_id=workspace.id,
                name="Healthy Team Runtime",
                team_type="software",
                default_task_policy={
                    "team_runtime": {
                        "status": "running",
                        "workspace_runtime_id": str(runtime.id),
                        "iteration_count": 4,
                        "last_heartbeat_at": now.isoformat(),
                        "scheduling_policy": {"loop_interval_seconds": 120},
                    }
                },
            ),
            AgentTeam(
                workspace_id=workspace.id,
                name="Stale Team Runtime",
                team_type="software",
                default_task_policy={
                    "team_runtime": {
                        "status": "running",
                        "iteration_count": 3,
                        "last_heartbeat_at": (now - timedelta(seconds=900)).isoformat(),
                    }
                },
            ),
        ]
    )
    session.add_all(
        [
            WorkerNode(
                worker_id="worker-online",
                worker_type="cloud",
                status="online",
                queue_name="agent_runs",
                capacity={"max_jobs": 2},
                details={},
                last_seen_at=now,
            ),
            WorkerNode(
                worker_id="worker-offline",
                worker_type="cloud",
                status="offline",
                queue_name="agent_runs",
                capacity={"max_jobs": 1},
                details={},
                last_seen_at=now,
            ),
            WorkerNode(
                worker_id="worker-stale",
                worker_type="cloud",
                status="online",
                queue_name="agent_runs",
                capacity={"max_jobs": 1},
                details={},
                last_seen_at=now - timedelta(seconds=600),
            ),
            WorkerLease(
                workspace_id=workspace.id,
                worker_id="worker-online",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=uuid4(),
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=now,
            ),
            WorkerLease(
                workspace_id=workspace.id,
                worker_id="worker-online",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=uuid4(),
                status="failed",
                attempt=1,
                lease_metadata={},
                started_at=now - timedelta(seconds=30),
                finished_at=now,
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="storage_mb",
                limit_value=100,
                reserved_value=80,
                unit="mb",
                status="active",
            ),
            AgentRun(
                workspace_id=workspace.id,
                runtime_id=runtime.id,
                status="running",
            ),
        ]
    )
    session.commit()
    return workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
