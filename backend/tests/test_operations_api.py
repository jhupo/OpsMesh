from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent, RuntimeSpaceQuota
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_operations_endpoints_expose_metrics_and_cleanup() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    keys = RedisKeyBuilder("chaincloud")
    failed_run_id = uuid4()
    queued_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=failed_run_id,
        idempotency_key=f"agent.run:{workspace.id}:queued",
    )
    other_workspace_queued_job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="agent.run:other:queued",
    )
    dead_letter_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=failed_run_id,
        idempotency_key=f"agent.run:{workspace.id}:dead-letter",
        attempt=3,
        max_attempts=3,
    )
    other_workspace_job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="agent.run:other:dead-letter",
        attempt=3,
        max_attempts=3,
    )
    redis.rpush(keys.queue("agent_runs"), queued_job.model_dump_json())
    redis.rpush(keys.queue("agent_runs"), other_workspace_queued_job.model_dump_json())
    redis.rpush(keys.dead_letter_queue("agent_runs"), dead_letter_job.model_dump_json())
    redis.rpush(keys.dead_letter_queue("agent_runs"), other_workspace_job.model_dump_json())
    redis.set(keys.idempotency_key(str(workspace.id), "job-1"), "1")
    redis.set(keys.idempotency_key(str(other_workspace_queued_job.workspace_id), "job-2"), "1")

    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="runtime",
        status="running",
        connection_status="online",
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=1_000),
    )
    failed_run = AgentRun(
        id=failed_run_id,
        workspace_id=workspace.id,
        status="failed",
        error={"message": "bad"},
    )
    session.add_all([runtime, failed_run])
    session.flush()
    session.add_all(
        [
            RuntimeEvent(
                workspace_id=workspace.id,
                workspace_runtime_id=runtime.id,
                event_type="runtime.failed",
                message="bad",
                created_at=datetime.now(UTC),
            ),
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=failed_run.id,
                event_type="run.failed",
                sequence=1,
                message="bad",
                created_at=datetime.now(UTC),
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="approval.rejected",
                target_type="approval",
                target_id="approval-1",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()

    heartbeat = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/worker-heartbeats",
        headers=_headers(owner.id),
        json={
            "worker_id": "worker-1",
            "worker_version": "2026.05.19",
            "hostname": "host-a",
            "capacity": {"max_jobs": 2},
            "details": {"pid": 123},
        },
    )
    assert heartbeat.status_code == 200
    workers = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/workers",
        headers=_headers(owner.id),
    )
    assert workers.status_code == 200
    assert workers.json()["total"] == 1
    assert workers.json()["items"][0]["worker_id"] == "worker-1"
    assert workers.json()["items"][0]["capacity"] == {"max_jobs": 2, "worker_type": "cloud"}

    drain = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/workers/worker-1/drain",
        headers=_headers(owner.id),
    )
    assert drain.status_code == 200
    assert drain.json()["status"] == "draining"

    metrics = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-metrics",
        headers=_headers(owner.id),
    )
    assert metrics.status_code == 200
    assert metrics.json()["queued"] == 1
    assert metrics.json()["dead_letter"] == 1
    assert metrics.json()["idempotency_keys"] == 1

    dead_letters = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/dead-letter-jobs",
        headers=_headers(owner.id),
    )
    assert dead_letters.status_code == 200
    assert dead_letters.json()["total"] == 1
    assert dead_letters.json()["items"][0]["job_id"] == str(dead_letter_job.job_id)

    cross_workspace_requeue = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/dead-letter-jobs/"
        f"{other_workspace_job.job_id}/requeue",
        headers=_headers(owner.id),
    )
    assert cross_workspace_requeue.status_code == 404

    requeued = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/dead-letter-jobs/"
        f"{dead_letter_job.job_id}/requeue",
        headers=_headers(owner.id),
    )
    assert requeued.status_code == 200
    assert requeued.json()["requeued"] is True
    assert requeued.json()["job"]["attempt"] == 0
    assert redis.llen(keys.dead_letter_queue("agent_runs")) == 1
    assert redis.llen(keys.queue("agent_runs")) == 3

    missing_requeue = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/dead-letter-jobs/"
        f"{dead_letter_job.job_id}/requeue",
        headers=_headers(owner.id),
    )
    assert missing_requeue.status_code == 404

    failed = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/failed-runs",
        headers=_headers(owner.id),
    )
    assert failed.status_code == 200
    assert failed.json()["total"] == 1

    run_events = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/run-events?event_type=run.failed",
        headers=_headers(owner.id),
    )
    assert run_events.status_code == 200
    assert run_events.json()["total"] == 1

    runtime_events = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-events?event_type=runtime.failed",
        headers=_headers(owner.id),
    )
    assert runtime_events.status_code == 200
    assert runtime_events.json()["total"] == 1

    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-events?action=approval.rejected",
        headers=_headers(owner.id),
    )
    assert audit.status_code == 200
    assert audit.json()["total"] == 1

    cleanup = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-cleanup?stale_after_seconds=60",
        headers=_headers(owner.id),
    )
    assert cleanup.status_code == 200
    assert cleanup.json()["stale_marked_offline"] == 1
    session.refresh(runtime)
    assert runtime.connection_status == "offline"
    space_event = session.query(RuntimeSpaceEvent).filter_by(
        runtime_space_id=runtime_space.id,
        event_type="runtime.marked_offline",
    ).one()
    assert space_event.event_metadata["runtime_id"] == str(runtime.id)


def test_operations_overview_uses_workspace_scoped_short_cache() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    other_user, other_workspace = _seed_workspace_with_role(
        session,
        email="other-cache@example.com",
        slug="other-cache",
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/overview",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    assert response.json()["failed_runs"] == 0

    session.add(
        AgentRun(
            workspace_id=workspace.id,
            status="failed",
            error={"message": "late failure"},
        )
    )
    session.commit()

    cached_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/overview",
        headers=_headers(owner.id),
    )
    other_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/overview",
        headers=_headers(other_user.id),
    )

    assert cached_response.status_code == 200
    assert cached_response.json()["failed_runs"] == 0
    assert other_response.status_code == 200
    assert other_response.json()["failed_runs"] == 0

    redis.delete(f"chaincloud:cache:api:overview:{workspace.id}:agent_runs")
    refreshed_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/overview",
        headers=_headers(owner.id),
    )

    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["failed_runs"] == 1


def test_operations_capacity_reports_queue_workers_and_runtime_space_saturation() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    other_user, other_workspace = _seed_workspace_with_role(
        session,
        email="other-capacity@example.com",
        slug="other-capacity",
    )
    keys = RedisKeyBuilder("chaincloud")
    old_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="old",
        priority=2,
        created_at=datetime.now(UTC) - timedelta(seconds=90),
    )
    new_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="new",
        priority=8,
        created_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    other_job = JobPayload(
        workspace_id=other_workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="other",
        priority=99,
        created_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    redis.rpush(keys.queue("agent_runs"), old_job.model_dump_json())
    redis.rpush(keys.queue("agent_runs"), new_job.model_dump_json())
    redis.rpush(keys.queue("agent_runs"), other_job.model_dump_json())

    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=2,
        reserved_value=2,
        unit="count",
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="runtime",
        status="running",
        connection_status="online",
    )
    worker_a = WorkerNode(
        worker_id="worker-a",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 3},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    worker_b = WorkerNode(
        worker_id="worker-b",
        worker_type="self_hosted",
        status="draining",
        queue_name="agent_runs",
        capacity={"max_jobs": 1},
        details={},
        drain_requested_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-a",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    session.add_all([quota, runtime, worker_a, worker_b, lease])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/capacity",
        headers=_headers(owner.id),
    )
    other_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/capacity",
        headers=_headers(other_user.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"]["queued"] == 2
    assert payload["queue"]["highest_priority"] == 8
    assert payload["queue"]["oldest_age_seconds"] >= 80
    assert payload["queue"]["newest_age_seconds"] >= 20
    assert payload["worker_capacity"] == {
        "workers_total": 2,
        "workers_online": 1,
        "workers_draining": 1,
        "workers_offline": 0,
        "max_jobs": 4,
        "running_jobs": 1,
        "available_slots": 3,
        "utilization": 0.25,
    }
    assert payload["runtime_spaces"][0]["runtime_space_id"] == str(runtime_space.id)
    assert payload["runtime_spaces"][0]["active_runtimes"] == 1
    assert payload["runtime_spaces"][0]["saturated"] is True
    assert payload["runtime_spaces"][0]["quotas"] == [
        {
            "quota_key": "active_runs",
            "limit_value": 2,
            "reserved_value": 2,
            "unit": "count",
            "utilization": 1.0,
            "saturated": True,
        }
    ]
    assert other_response.status_code == 200
    assert other_response.json()["queue"]["queued"] == 1
    assert other_response.json()["runtime_spaces"] == []


def test_operations_lists_worker_leases_by_workspace() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    other_user, other_workspace = _seed_workspace_with_role(
        session,
        email="other@example.com",
        slug="other",
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-1",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={"task": "owned"},
        started_at=datetime.now(UTC),
    )
    other_lease = WorkerLease(
        workspace_id=other_workspace.id,
        worker_id="worker-2",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={"task": "other"},
        started_at=datetime.now(UTC),
    )
    session.add_all([lease, other_lease])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/worker-leases?status=running",
        headers=_headers(owner.id),
    )
    other_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/worker-leases",
        headers=_headers(other_user.id),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["job_id"] == str(lease.job_id)
    assert other_response.status_code == 200
    assert other_response.json()["total"] == 1
    assert other_response.json()["items"][0]["job_id"] == str(other_lease.job_id)


def test_operator_can_use_operations_but_viewer_cannot() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    operator, workspace = _seed_workspace_with_role(
        session,
        email="operator@example.com",
        slug="operator-space",
        role="operator",
    )
    viewer = User(email="viewer@example.com", display_name="viewer")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace=workspace, user=viewer, role="viewer"))
    session.commit()

    operator_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-metrics",
        headers=_headers(operator.id),
    )
    viewer_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-metrics",
        headers=_headers(viewer.id),
    )

    assert operator_response.status_code == 200
    assert viewer_response.status_code == 403


def test_security_events_are_recorded_and_queryable_for_workspace_denials() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    outsider, _ = _seed_workspace_with_role(
        session,
        email="outsider@example.com",
        slug="outsider-space",
        role="owner",
    )

    denied = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-metrics",
        headers=_headers(outsider.id),
    )
    assert denied.status_code == 403

    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/security-events"
        "?action=auth.workspace.rejected",
        headers=_headers(owner.id),
    )
    assert events.status_code == 200
    payload = events.json()
    assert payload["total"] == 1
    assert payload["items"][0]["workspace_id"] == str(workspace.id)
    assert payload["items"][0]["user_id"] == str(outsider.id)
    assert payload["items"][0]["severity"] == "warning"
    assert payload["items"][0]["event_metadata"]["required_action"] == "operate"


def test_invalid_internal_token_records_security_event() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, seed_session = _client(redis)
    user, _ = _seed_workspace(seed_session)

    response = client.get(
        "/api/v1/workspaces",
        headers={"Authorization": "Bearer wrong-token", "X-User-ID": str(user.id)},
    )

    assert response.status_code == 401
    event = seed_session.query(SecurityEvent).filter_by(action="auth.internal_token.rejected").one()
    assert event.workspace_id is None
    assert event.outcome == "denied"
    assert event.severity == "warning"
    assert event.path == "/api/v1/workspaces"


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
    session = session_factory()
    app = create_app(Settings(environment="test", log_format="text", internal_api_token=TOKEN))

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), session


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    return _seed_workspace_with_role(session)


def _seed_workspace_with_role(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner",
    role: str = "owner",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
