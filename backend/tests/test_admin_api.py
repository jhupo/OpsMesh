from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.approvals.models import Approval
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent, RuntimeSpaceQuota
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"


def test_admin_api_requires_platform_admin_token() -> None:
    client, session, _ = _client()
    _seed_workspace(session)

    missing = client.get("/api/v1/admin/overview")
    wrong = client.get("/api/v1/admin/overview", headers={"Authorization": "Bearer wrong"})
    accepted = client.get("/api/v1/admin/overview", headers=_admin_headers())

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    rejected_events = session.query(SecurityEvent).filter_by(
        action="auth.platform_admin.rejected",
    )
    assert rejected_events.count() == 2


def test_admin_api_exposes_global_control_plane_metadata() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
        status="active",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-1",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        worker_version="2026.05.19",
        hostname="host-a",
        capacity={"max_jobs": 2},
        details={},
        last_seen_at=datetime.now(UTC),
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
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    security_event = SecurityEvent(
        workspace_id=other_workspace.id,
        user_id=None,
        action="runtime.policy.violation",
        outcome="denied",
        severity="critical",
        path="/api/v1/workspaces/x/runtimes",
        method="POST",
        reason="bad runtime",
        event_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker, lease, security_event])
    session.commit()

    overview = client.get("/api/v1/admin/overview", headers=_admin_headers())
    workers = client.get("/api/v1/admin/workers", headers=_admin_headers())
    leases = client.get("/api/v1/admin/worker-leases", headers=_admin_headers())
    spaces = client.get("/api/v1/admin/runtime-spaces", headers=_admin_headers())
    events = client.get("/api/v1/admin/security-events?severity=critical", headers=_admin_headers())
    workspaces = client.get("/api/v1/admin/workspaces", headers=_admin_headers())

    assert overview.status_code == 200
    assert overview.json()["workspaces_total"] == 2
    assert overview.json()["workers_online"] == 1
    assert overview.json()["active_worker_leases"] == 1
    assert overview.json()["critical_security_events"] == 1
    assert workers.status_code == 200
    assert workers.json()["items"][0]["worker_id"] == "worker-1"
    assert leases.status_code == 200
    assert leases.json()["items"][0]["workspace_id"] == str(workspace.id)
    assert spaces.status_code == 200
    assert spaces.json()["items"][0]["id"] == str(runtime_space.id)
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert workspaces.status_code == 200
    assert workspaces.json()["total"] == 2


def test_admin_can_drain_worker_and_quarantine_runtime_space() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Unsafe Space",
        scope="workspace",
        status="active",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-1",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker])
    session.commit()

    drained = client.post("/api/v1/admin/workers/worker-1/drain", headers=_admin_headers())
    quarantined = client.post(
        f"/api/v1/admin/runtime-spaces/{runtime_space.id}/quarantine",
        headers=_admin_headers(),
        json={"reason": "Suspicious egress"},
    )

    session.refresh(worker)
    session.refresh(runtime_space)
    event = session.query(RuntimeSpaceEvent).one()

    assert drained.status_code == 200
    assert drained.json()["status"] == "draining"
    assert worker.status == "draining"
    assert quarantined.status_code == 200
    assert quarantined.json()["status"] == "quarantined"
    assert quarantined.json()["reason"] == "Suspicious egress"
    assert runtime_space.status == "quarantined"
    assert event.event_type == "runtime_space.quarantined"


def test_admin_can_update_worker_governance_capacity_and_status() -> None:
    client, session, _ = _client()
    worker = WorkerNode(
        worker_id="worker-governed",
        worker_type="cloud",
        status="draining",
        queue_name="agent_runs",
        capacity={"max_jobs": 1, "worker_type": "cloud"},
        details={"region": "sg"},
        last_seen_at=datetime.now(UTC),
        drain_requested_at=datetime.now(UTC),
    )
    session.add(worker)
    session.commit()

    updated = client.patch(
        "/api/v1/admin/workers/worker-governed",
        headers=_admin_headers(),
        json={
            "status": "online",
            "worker_type": "self_hosted",
            "worker_version": "2026.05.20",
            "hostname": "node-a",
            "capacity": {
                "max_jobs": 4,
                "runtime_modes": ["self_hosted", "docker"],
                "capabilities": ["code.execute", "image.generate"],
                "memory_mb": 8192,
            },
            "details": {"region": "sg", "pool": "premium"},
            "reason": "Resize worker pool",
            "updated_by": "ops-admin",
        },
    )

    session.refresh(worker)
    policy = session.query(PlatformPolicy).filter_by(policy_key="global_worker_control").one()
    events = (
        session.query(PlatformPolicyEvent)
        .filter_by(platform_policy_id=policy.id)
        .order_by(PlatformPolicyEvent.created_at)
        .all()
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "online"
    assert updated.json()["worker_type"] == "self_hosted"
    assert updated.json()["capacity"]["max_jobs"] == 4
    assert updated.json()["capacity"]["worker_type"] == "self_hosted"
    assert updated.json()["capacity"]["runtime_modes"] == ["self_hosted", "docker"]
    assert worker.drain_requested_at is None
    assert worker.details == {"region": "sg", "pool": "premium"}
    assert [event.event_type for event in events] == ["platform_policy.created", "worker.updated"]
    assert events[-1].event_metadata["reason"] == "Resize worker pool"
    assert events[-1].event_metadata["updated_by"] == "ops-admin"
    assert events[-1].event_metadata["changed_fields"] == [
        "status",
        "worker_type",
        "worker_version",
        "hostname",
        "capacity",
        "details",
    ]


def test_admin_can_manage_global_queue_runtime_and_risky_execution_policy() -> None:
    client, session, redis = _client()
    _, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Team runtime",
        status="running",
        connection_status="online",
        docker_container_id="container-123",
        limits={"cpu_count": 1, "memory_mb": 512},
        network_policy={"disabled": True},
        capabilities={},
    )
    session.add(runtime)
    session.commit()
    queue = RedisQueue(redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    queued = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queued-job",
    )
    dead = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.RUNTIME_CLEANUP,
        resource_id=runtime.id,
        idempotency_key="dead-job",
        attempt=2,
    )
    queue.enqueue(queued)
    queue.retry_or_dead_letter(dead)

    metrics = client.get("/api/v1/admin/queues/agent_runs/metrics", headers=_admin_headers())
    dead_letters = client.get(
        "/api/v1/admin/queues/agent_runs/dead-letter-jobs",
        headers=_admin_headers(),
    )
    requeued = client.post(
        f"/api/v1/admin/queues/agent_runs/dead-letter-jobs/{dead.job_id}/requeue",
        headers=_admin_headers(),
    )
    runtimes = client.get("/api/v1/admin/runtimes", headers=_admin_headers())
    stopped = client.post(
        f"/api/v1/admin/runtimes/{runtime.id}/force-stop",
        headers=_admin_headers(),
        json={"reason": "Operator safety stop"},
    )
    policy = client.get(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
    )
    updated_policy = client.patch(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
        json={
            "value": {
                "allow_runtime_commands": True,
                "allow_network_egress": False,
                "high_risk_tool_mode": "allow",
                "unknown": True,
            },
            "description": "Test policy",
            "updated_by": "admin-test",
        },
    )

    session.refresh(runtime)
    runtime_event = session.query(RuntimeEvent).filter_by(workspace_runtime_id=runtime.id).one()

    assert metrics.status_code == 200
    assert metrics.json()["queued"] == 1
    assert metrics.json()["dead_letter"] == 1
    assert dead_letters.status_code == 200
    assert dead_letters.json()["total"] == 1
    assert requeued.status_code == 200
    assert requeued.json()["requeued"] is True
    admin_queue = RedisQueue(redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    assert admin_queue.count_dead_letters() == 0
    assert runtimes.status_code == 200
    assert runtimes.json()["items"][0]["id"] == str(runtime.id)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert runtime.status == "stopped"
    assert runtime.connection_status == "offline"
    assert runtime_event.event_type == "runtime.force_stopped"
    assert policy.status_code == 200
    assert policy.json()["policy_key"] == "global_risky_execution"
    assert updated_policy.status_code == 200
    assert updated_policy.json()["description"] == "Test policy"
    assert updated_policy.json()["updated_by"] == "admin-test"
    assert updated_policy.json()["value"]["allow_runtime_commands"] is True
    assert updated_policy.json()["value"]["high_risk_tool_mode"] == "allow"
    assert updated_policy.json()["value"]["require_approval_for_high_risk_tools"] is False
    assert "unknown" not in updated_policy.json()["value"]


def test_admin_operations_summary_aggregates_queue_capacity_and_blockers() -> None:
    client, session, redis = _client()
    _, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="team",
        status="active",
        policy={},
        network_policy={},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-summary",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 3},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "model_timeout"},
    )
    waiting_run = AgentRun(
        workspace_id=workspace.id,
        status="waiting_approval",
        input={},
    )
    waiting_task = Task(
        workspace_id=workspace.id,
        title="Needs approval",
        status="waiting_approval",
    )
    approval = Approval(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=waiting_run.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={},
        status="pending",
        created_at=datetime.now(UTC),
    )
    security_event = SecurityEvent(
        workspace_id=workspace.id,
        user_id=None,
        action="mcp_tool.blocked",
        outcome="blocked",
        severity="high",
        path="internal:mcp",
        method="WORKER",
        reason="mcp_tool_not_allowed",
        event_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker, failed_run, waiting_run, waiting_task])
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=4,
        reserved_value=2,
        unit="count",
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id=worker.worker_id,
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=waiting_run.id,
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    approval.agent_run_id = waiting_run.id
    session.add_all([quota, lease, approval, security_event])
    session.commit()
    queue = RedisQueue(redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    high_priority_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="high-priority",
        priority=9,
    )
    low_priority_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="low-priority",
        priority=1,
    )
    queue.enqueue(low_priority_job)
    queue.enqueue(high_priority_job)

    response = client.get(
        "/api/v1/admin/operations/summary?queue_name=agent_runs",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["queued"] == 2
    assert body["queue"]["highest_priority"] == 9
    assert body["queue"]["oldest_queued_at"] is not None
    assert body["workers"]["total"] == 1
    assert body["workers"]["running_leases"] == 1
    assert body["workers"]["total_capacity"] == 3
    assert body["workers"]["available_capacity"] == 2
    assert body["runtime_spaces"]["quota_usage"]["active_runs"]["limit_value"] == 4
    assert body["runtime_spaces"]["quota_usage"]["active_runs"]["reserved_value"] == 2
    assert body["approvals"]["pending"] == 1
    assert body["approvals"]["runs_waiting"] == 1
    assert body["approvals"]["tasks_waiting"] == 1
    assert body["failures"]["failed_runs"] == 1
    assert body["failures"]["top_run_error_codes"] == [
        {"key": "model_timeout", "count": 1}
    ]
    assert body["failures"]["top_security_reasons"] == [
        {"key": "mcp_tool_not_allowed", "count": 1}
    ]


def test_admin_system_configuration_exposes_redacted_resource_summary() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/admin/system/configuration", headers=_admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["environment"] == "test"
    assert body["settings"]["database_url"].startswith("postgresql+psycopg://***:***@")
    assert "test-token" not in str(body)
    assert "admin-token" not in str(body)
    assert body["recommended_resources"]["cpu_count"] >= 1
    assert body["configured_resources"]["database_pool_size"] >= 1
    assert set(body["resource_deltas"]) == {
        "database_pool_size",
        "database_max_overflow",
        "blocking_thread_pool_workers",
        "redis_max_connections",
    }


def _client() -> tuple[TestClient, Session, fakeredis.FakeRedis]:
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
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            platform_admin_token=ADMIN_TOKEN,
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), session, redis


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
