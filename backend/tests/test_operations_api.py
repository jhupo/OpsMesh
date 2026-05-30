from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.admin.models import PlatformPolicy
from backend.app.approvals.models import Approval
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import McpServer
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
from backend.app.runtimes.models import RuntimeEvent, RuntimeLease, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.tasks.models import Task, TaskStep
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
    stale_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-stale",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={"scope": "owned"},
        started_at=datetime.now(UTC) - timedelta(seconds=1_000),
    )
    other_workspace_stale_lease = WorkerLease(
        workspace_id=uuid4(),
        worker_id="worker-other-stale",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={"scope": "other"},
        started_at=datetime.now(UTC) - timedelta(seconds=1_000),
    )
    session.add_all(
        [
            stale_lease,
            other_workspace_stale_lease,
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
            "capacity": {"max_jobs": 2, "token": "capacity-secret"},
            "details": {"pid": 123, "headers": {"authorization": "Bearer hidden"}},
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["details"] == {"pid": 123, "headers": "[redacted]"}
    workers = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/workers",
        headers=_headers(owner.id),
    )
    assert workers.status_code == 200
    assert workers.json()["total"] == 1
    assert workers.json()["items"][0]["worker_id"] == "worker-1"
    assert workers.json()["items"][0]["capacity"] == {
        "max_jobs": 2,
        "token": "[redacted]",
        "worker_type": "cloud",
    }
    assert workers.json()["items"][0]["details"] == {
        "pid": 123,
        "headers": "[redacted]",
    }

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
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-cleanup"
        "?stale_after_seconds=60&stale_lease_after_seconds=60",
        headers=_headers(owner.id),
    )
    cleanup_again = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-cleanup"
        "?stale_after_seconds=60&stale_lease_after_seconds=60",
        headers=_headers(owner.id),
    )
    assert cleanup.status_code == 200
    assert cleanup.json()["stale_marked_offline"] == 1
    assert cleanup.json()["expired_worker_leases"] == 1
    assert cleanup_again.status_code == 200
    assert cleanup_again.json()["expired_worker_leases"] == 0
    session.refresh(runtime)
    session.refresh(stale_lease)
    session.refresh(other_workspace_stale_lease)
    assert runtime.connection_status == "offline"
    assert stale_lease.status == "expired"
    assert stale_lease.finished_at is not None
    assert stale_lease.lease_metadata["scope"] == "owned"
    assert stale_lease.lease_metadata["expired_by"] == "worker_maintenance"
    assert other_workspace_stale_lease.status == "running"
    space_event = session.query(RuntimeSpaceEvent).filter_by(
        runtime_space_id=runtime_space.id,
        event_type="runtime.marked_offline",
    ).one()
    assert space_event.event_metadata["runtime_id"] == str(runtime.id)


def test_operations_stale_runs_diagnostics_and_recovery_are_workspace_scoped() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace_with_role(
        session,
        email="other-stale-runs@example.com",
        slug="other-stale-runs",
    )
    now = datetime.now(UTC)
    old = now - timedelta(hours=2)
    fresh = now - timedelta(seconds=30)
    queued_run = AgentRun(
        workspace_id=workspace.id,
        status="queued",
        input={"token": "queued-secret-token"},
        created_at=old,
        updated_at=old,
    )
    running_run = AgentRun(
        workspace_id=workspace.id,
        status="running",
        input={"base_url": "https://secret.example.test"},
        started_at=old,
        created_at=old,
        updated_at=old,
    )
    waiting_run = AgentRun(
        workspace_id=workspace.id,
        status="waiting_runtime",
        input={"headers": {"authorization": "Bearer secret"}},
        started_at=old,
        created_at=old,
        updated_at=old,
    )
    fresh_run = AgentRun(
        workspace_id=workspace.id,
        status="running",
        started_at=fresh,
        created_at=fresh,
        updated_at=fresh,
    )
    other_run = AgentRun(
        workspace_id=other_workspace.id,
        status="running",
        started_at=old,
        created_at=old,
        updated_at=old,
    )
    session.add_all([queued_run, running_run, waiting_run, fresh_run, other_run])
    session.flush()
    running_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-stale-run",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type=JobType.AGENT_RUN.value,
        resource_id=running_run.id,
        status="running",
        attempt=1,
        lease_metadata={
            "token": "lease-secret-token",
            "container_id": "container-secret",
        },
        started_at=old,
    )
    session.add(running_lease)
    session.commit()

    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/stale-runs"
        "?stale_after_seconds=900",
        headers=_headers(owner.id),
    )

    assert diagnostics.status_code == 200
    payload = diagnostics.json()
    assert payload["total"] == 3
    run_ids = {item["run_id"] for item in payload["items"]}
    assert run_ids == {str(queued_run.id), str(running_run.id), str(waiting_run.id)}
    assert str(fresh_run.id) not in run_ids
    assert str(other_run.id) not in run_ids
    assert "queued-secret-token" not in str(payload)
    assert "secret.example.test" not in str(payload)
    assert "lease-secret-token" not in str(payload)
    assert "container-secret" not in str(payload)
    running_item = next(item for item in payload["items"] if item["run_id"] == str(running_run.id))
    assert running_item["worker_id"] == "worker-stale-run"
    assert running_item["worker_lease_status"] == "running"

    recovered = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/stale-runs/recover",
        headers=_headers(owner.id),
        json={
            "stale_after_seconds": 900,
            "statuses": ["queued", "running", "waiting_runtime"],
            "limit": 10,
            "reason": "operator stale recovery",
        },
    )

    assert recovered.status_code == 200
    recovered_payload = recovered.json()
    assert recovered_payload["scanned_runs"] == 3
    assert recovered_payload["requeued_runs"] == 1
    assert recovered_payload["failed_closed_runs"] == 2
    assert recovered_payload["expired_worker_leases"] == 1
    keys = RedisKeyBuilder("chaincloud")
    assert redis.llen(keys.queue("agent_runs")) == 1
    session.expire_all()
    stored_running = session.get(AgentRun, running_run.id)
    stored_waiting = session.get(AgentRun, waiting_run.id)
    stored_queued = session.get(AgentRun, queued_run.id)
    stored_fresh = session.get(AgentRun, fresh_run.id)
    stored_other = session.get(AgentRun, other_run.id)
    stored_lease = session.get(WorkerLease, running_lease.id)
    assert stored_running is not None
    assert stored_waiting is not None
    assert stored_queued is not None
    assert stored_fresh is not None
    assert stored_other is not None
    assert stored_lease is not None
    assert stored_running.status == "failed"
    assert stored_waiting.status == "failed"
    assert stored_queued.status == "queued"
    assert stored_fresh.status == "running"
    assert stored_other.status == "running"
    assert stored_lease.status == "expired"
    assert stored_lease.lease_metadata["expired_by"] == "stale_run_recovery"
    audit_event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "worker.stale_runs_recovered",
        )
    )
    assert audit_event is not None
    assert audit_event.audit_metadata["scanned_runs"] == 3


def test_operations_runtime_events_redact_sensitive_metadata() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="runtime",
        docker_container_id="container-secret",
    )
    session.add(runtime)
    session.flush()
    session.add(
        RuntimeEvent(
            workspace_id=workspace.id,
            workspace_runtime_id=runtime.id,
            event_type="runtime.cleanup",
            message="cleanup",
            event_metadata={
                "cleanup": {
                    "container_id": "container-secret",
                    "base_url": "https://runtime.example.test/private",
                    "success": True,
                },
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-events"
        "?event_type=runtime.cleanup",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "cleanup": {
            "container_id": "[redacted]",
            "base_url": "[redacted]",
            "success": True,
        },
        "safe": "visible",
    }
    assert "container-secret" not in str(metadata)
    assert "runtime.example.test/private" not in str(metadata)


def test_operations_audit_events_redact_sensitive_metadata() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    session.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_type="user",
            actor_id=str(owner.id),
            user_id=owner.id,
            action="workspace.audit_sensitive",
            target_type="workspace",
            target_id=str(workspace.id),
            audit_metadata={
                "token": "audit-token",
                "nested": {"headers": {"authorization": "Bearer hidden"}},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-events"
        "?action=workspace.audit_sensitive",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["audit_metadata"]
    assert metadata == {
        "token": "[redacted]",
        "nested": {"headers": "[redacted]"},
        "safe": "visible",
    }
    assert "audit-token" not in str(metadata)
    assert "Bearer hidden" not in str(metadata)


def test_worker_heartbeat_capacity_is_bounded_by_platform_policy() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    session.add(
        PlatformPolicy(
            policy_key="global_worker_control",
            status="active",
            value={
                "managed_by": "platform_admin",
                "allow_status_updates": True,
                "allow_capacity_updates": True,
                "allow_queue_updates": True,
                "allowed_statuses": ["online", "draining"],
                "allowed_worker_types": ["cloud"],
                "max_capacity": {"max_jobs": 2, "memory_mb": 4096},
            },
            description="Cap self-reported workers",
        )
    )
    session.commit()

    heartbeat = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/worker-heartbeats",
        headers=_headers(owner.id),
        json={
            "worker_id": "worker-capped",
            "capacity": {"max_jobs": 8, "memory_mb": 16384, "disk_mb": 100000},
            "details": {},
        },
    )

    worker = session.scalar(select(WorkerNode).where(WorkerNode.worker_id == "worker-capped"))

    assert heartbeat.status_code == 200
    assert worker is not None
    assert worker.capacity == {
        "max_jobs": 2,
        "memory_mb": 4096,
        "disk_mb": 100000,
        "worker_type": "cloud",
    }


def test_operations_worker_status_control_quarantines_and_resumes_worker() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    worker = WorkerNode(
        worker_id="worker-control",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 2, "token": "hidden"},
        details={"headers": {"authorization": "Bearer hidden"}},
        last_seen_at=datetime.now(UTC),
    )
    session.add(worker)
    session.commit()

    quarantined = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/workers/worker-control/status",
        headers=_headers(owner.id),
        json={"status": "quarantined", "reason": "suspicious runtime output"},
    )
    heartbeat = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/worker-heartbeats",
        headers=_headers(owner.id),
        json={"worker_id": "worker-control", "status": "online", "details": {}},
    )
    assert quarantined.status_code == 200
    assert quarantined.json()["status"] == "quarantined"
    assert quarantined.json()["capacity"]["token"] == "[redacted]"
    assert quarantined.json()["details"]["headers"] == "[redacted]"
    assert heartbeat.status_code == 200
    session.refresh(worker)
    assert worker.status == "quarantined"

    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/workers/worker-control/status",
        headers=_headers(owner.id),
        json={"status": "online", "reason": "operator reviewed"},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/workers/missing/status",
        headers=_headers(owner.id),
        json={"status": "disabled"},
    )

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "online"
    assert missing.status_code == 404
    audit_events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "worker.status_updated",
        )
    ).all()
    assert [event.audit_metadata["status"] for event in audit_events] == [
        "quarantined",
        "online",
    ]


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


def test_operations_capacity_uses_workspace_scoped_short_cache() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)

    first_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/capacity",
        headers=_headers(owner.id),
    )
    assert first_response.status_code == 200
    assert first_response.json()["runtime_spaces"] == []

    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Cached Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.commit()

    cached_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/capacity",
        headers=_headers(owner.id),
    )
    assert cached_response.status_code == 200
    assert cached_response.json()["runtime_spaces"] == []

    redis.delete(f"chaincloud:cache:api:capacity:{workspace.id}:agent_runs")
    refreshed_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/capacity",
        headers=_headers(owner.id),
    )
    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["runtime_spaces"][0]["runtime_space_id"] == str(
        runtime_space.id
    )


def test_operations_aggregates_return_zero_metrics_for_empty_workspace() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace_with_role(
        session,
        email="empty-ops@example.com",
        slug="empty-ops",
    )
    headers = _headers(owner.id)

    overview = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/overview",
        headers=headers,
    )
    capacity = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/capacity",
        headers=headers,
    )
    scheduler = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler",
        headers=headers,
    )
    outcomes = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/outcomes",
        headers=headers,
    )
    runtime_capacity = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-capacity",
        headers=headers,
    )
    control_plane = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/control-plane",
        headers=headers,
    )

    for response in (
        overview,
        capacity,
        scheduler,
        outcomes,
        runtime_capacity,
        control_plane,
    ):
        assert response.status_code == 200

    assert overview.json() == {
        "queue": {
            "queue_name": "agent_runs",
            "queued": 0,
            "dead_letter": 0,
            "idempotency_keys": 0,
        },
        "failed_runs": 0,
        "offline_runtimes": 0,
        "workers_online": 0,
        "security_warnings": 0,
    }
    assert capacity.json()["worker_capacity"]["workers_total"] == 0
    assert capacity.json()["worker_capacity"]["available_slots"] == 0
    assert capacity.json()["runtime_spaces"] == []
    assert scheduler.json()["backlog"] == {
        "queued_steps": 0,
        "running_steps": 0,
        "waiting_approval_tasks": 0,
        "blocked_steps": 0,
        "active_runs": 0,
        "oldest_queued_age_seconds": None,
        "highest_priority": None,
    }
    assert scheduler.json()["priority_buckets"] == []
    assert scheduler.json()["blocked_reasons"] == []
    assert outcomes.json()["runs"]["total_runs"] == 0
    assert outcomes.json()["runs"]["failure_rate"] == 0
    assert outcomes.json()["approvals"]["pending"] == 0
    assert runtime_capacity.json()["providers"] == []
    assert runtime_capacity.json()["worker_types"] == []
    assert control_plane.json()["health"] == "critical"
    assert [issue["code"] for issue in control_plane.json()["issues"]] == [
        "worker_fleet_empty"
    ]


def test_operations_queue_insights_reports_priority_and_type_buckets() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    keys = RedisKeyBuilder("chaincloud")
    now = datetime.now(UTC)
    old_high = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-insights:high-old",
        priority=8,
        created_at=now - timedelta(seconds=120),
    )
    new_high = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-insights:high-new",
        priority=8,
        created_at=now - timedelta(seconds=20),
    )
    memory_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.MEMORY_INDEX,
        resource_id=uuid4(),
        idempotency_key="queue-insights:memory",
        priority=2,
        created_at=now - timedelta(seconds=60),
    )
    dead_letter = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-insights:dead",
        priority=8,
        attempt=3,
        max_attempts=3,
    )
    other_workspace = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-insights:other",
        priority=99,
    )
    redis.rpush(
        keys.queue("agent_runs"),
        old_high.model_dump_json(),
        new_high.model_dump_json(),
        memory_job.model_dump_json(),
        other_workspace.model_dump_json(),
    )
    redis.rpush(
        keys.dead_letter_queue("agent_runs"),
        dead_letter.model_dump_json(),
        other_workspace.model_dump_json(),
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-insights",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue_name"] == "agent_runs"
    assert payload["queued_total"] == 3
    assert payload["dead_letter_total"] == 1
    assert payload["queued_scanned"] == 3
    assert payload["dead_letter_scanned"] == 1
    assert payload["truncated"] is False
    assert payload["highest_priority"] == 8
    assert 100 <= payload["oldest_queued_age_seconds"] <= 130
    assert payload["priority_buckets"] == [
        {
            "priority": 8,
            "queued": 2,
            "dead_letter": 1,
            "oldest_queued_age_seconds": payload["priority_buckets"][0][
                "oldest_queued_age_seconds"
            ],
        },
        {
            "priority": 2,
            "queued": 1,
            "dead_letter": 0,
            "oldest_queued_age_seconds": payload["priority_buckets"][1][
                "oldest_queued_age_seconds"
            ],
        },
    ]
    assert 100 <= payload["priority_buckets"][0]["oldest_queued_age_seconds"] <= 130
    assert 40 <= payload["priority_buckets"][1]["oldest_queued_age_seconds"] <= 80
    assert payload["job_type_buckets"] == [
        {
            "job_type": "agent.run",
            "queued": 2,
            "dead_letter": 1,
            "highest_priority": 8,
            "oldest_queued_age_seconds": payload["job_type_buckets"][0][
                "oldest_queued_age_seconds"
            ],
        },
        {
            "job_type": "memory.index",
            "queued": 1,
            "dead_letter": 0,
            "highest_priority": 2,
            "oldest_queued_age_seconds": payload["job_type_buckets"][1][
                "oldest_queued_age_seconds"
            ],
        },
    ]


def test_operations_queue_insights_marks_truncated_scan() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    keys = RedisKeyBuilder("chaincloud")
    for index in range(3):
        redis.rpush(
            keys.queue("agent_runs"),
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=uuid4(),
                idempotency_key=f"queue-insights:truncated:{index}",
                priority=index,
            ).model_dump_json(),
        )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-insights?scan_limit=2",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued_total"] == 3
    assert payload["queued_scanned"] == 2
    assert payload["truncated"] is True


def test_operations_queue_governance_diagnoses_queue_run_drift() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    keys = RedisKeyBuilder("chaincloud")
    now = datetime.now(UTC)
    queued_run = AgentRun(
        workspace_id=workspace.id,
        status="queued",
        created_at=now - timedelta(seconds=120),
        updated_at=now - timedelta(seconds=120),
    )
    missing_run = AgentRun(
        workspace_id=workspace.id,
        status="queued",
        created_at=now - timedelta(seconds=180),
        updated_at=now - timedelta(seconds=180),
    )
    completed_run = AgentRun(workspace_id=workspace.id, status="completed")
    session.add_all([queued_run, missing_run, completed_run])
    session.flush()
    duplicate_first = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=queued_run.id,
        idempotency_key="queue-governance:queued:first",
        priority=3,
        created_at=now - timedelta(seconds=1_000),
    )
    duplicate_second = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=queued_run.id,
        idempotency_key="queue-governance:queued:second",
        priority=3,
        created_at=now - timedelta(seconds=900),
    )
    orphaned = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-governance:orphaned",
        created_at=now - timedelta(seconds=800),
    )
    non_runnable = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=completed_run.id,
        idempotency_key="queue-governance:completed",
        created_at=now - timedelta(seconds=700),
    )
    other_workspace = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-governance:other",
    )
    redis.rpush(
        keys.queue("agent_runs"),
        duplicate_first.model_dump_json(),
        duplicate_second.model_dump_json(),
        orphaned.model_dump_json(),
        non_runnable.model_dump_json(),
        other_workspace.model_dump_json(),
    )
    redis.rpush(
        keys.dead_letter_queue("agent_runs"),
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=queued_run.id,
            idempotency_key="queue-governance:dead",
        ).model_dump_json(),
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-governance"
        "?stale_after_seconds=600",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued_total"] == 4
    assert payload["queued_scanned"] == 4
    assert payload["agent_run_jobs_scanned"] == 4
    assert payload["orphaned_queue_jobs"] == 1
    assert payload["non_runnable_queue_jobs"] == 1
    assert payload["duplicate_queue_jobs"] == 1
    assert payload["queued_runs_missing_queue_job"] == 1
    assert payload["old_queued_jobs"] == 4
    assert payload["dead_letter_total"] == 1
    assert payload["recommended_actions"] == [
        "requeue_missing_runs",
        "remove_orphaned_jobs",
        "remove_non_runnable_jobs",
    ]
    issue_codes = [issue["code"] for issue in payload["issues"]]
    assert "orphaned_queue_jobs" in issue_codes
    assert "queued_runs_missing_queue_job" in issue_codes
    assert "dead_letter_pressure" in issue_codes


def test_operations_queue_governance_reconciles_missing_and_stale_jobs() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    keys = RedisKeyBuilder("chaincloud")
    queued_run = AgentRun(workspace_id=workspace.id, status="queued")
    completed_run = AgentRun(workspace_id=workspace.id, status="completed")
    session.add_all([queued_run, completed_run])
    session.flush()
    orphaned = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queue-governance-reconcile:orphaned",
    )
    non_runnable = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=completed_run.id,
        idempotency_key="queue-governance-reconcile:completed",
    )
    redis.rpush(
        keys.queue("agent_runs"),
        orphaned.model_dump_json(),
        non_runnable.model_dump_json(),
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/queue-governance/reconcile",
        headers=_headers(owner.id),
        json={
            "actions": [
                "requeue_missing_runs",
                "remove_orphaned_jobs",
                "remove_non_runnable_jobs",
            ],
            "reason": "operator repair",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requeued_missing_runs"] == 1
    assert payload["removed_orphaned_jobs"] == 1
    assert payload["removed_non_runnable_jobs"] == 1
    assert payload["remaining_issues"] == []
    queued_jobs = [
        JobPayload.model_validate_json(raw)
        for raw in redis.lrange(keys.queue("agent_runs"), 0, -1)
    ]
    assert [job.resource_id for job in queued_jobs] == [queued_run.id]
    assert queued_jobs[0].job_type == JobType.AGENT_RUN
    audit = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "operations.queue_governance_reconciled")
    )
    assert audit is not None
    assert audit.audit_metadata["requeued_missing_runs"] == 1
    assert audit.audit_metadata["removed_orphaned_jobs"] == 1
    assert audit.audit_metadata["removed_non_runnable_jobs"] == 1


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


def test_operations_runtime_capacity_reports_provider_and_worker_slots() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    other_user, other_workspace = _seed_workspace_with_role(
        session,
        email="other-runtime-capacity@example.com",
        slug="other-runtime-capacity",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    cloud_runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        runtime_provider="cloud_docker",
        runtime_type="docker",
        name="cloud",
        status="running",
        connection_status="online",
        capabilities={"slots": 2},
    )
    self_hosted_runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="local",
        status="active",
        connection_status="degraded",
        capabilities={"max_concurrent_jobs": 3},
    )
    other_runtime = WorkspaceRuntime(
        workspace_id=other_workspace.id,
        runtime_provider="cloud_docker",
        runtime_type="docker",
        name="other",
        status="running",
        connection_status="online",
    )
    session.add_all([cloud_runtime, self_hosted_runtime, other_runtime])
    session.flush()
    cloud_run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=cloud_runtime.id,
        status="running",
    )
    self_hosted_run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=self_hosted_runtime.id,
        status="queued",
    )
    session.add_all([cloud_run, self_hosted_run])
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="docker_runtimes",
        limit_value=2,
        reserved_value=1,
        unit="count",
    )
    cloud_worker = WorkerNode(
        worker_id="worker-cloud",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 4},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    local_worker = WorkerNode(
        worker_id="worker-local",
        worker_type="self_hosted",
        status="draining",
        queue_name="agent_runs",
        capacity={"max_jobs": 2},
        details={},
        drain_requested_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-local",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=self_hosted_run.id,
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    session.add_all(
        [
            quota,
            cloud_worker,
            local_worker,
            lease,
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-capacity",
        headers=_headers(owner.id),
    )
    other_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/runtime-capacity",
        headers=_headers(other_user.id),
    )

    assert response.status_code == 200
    payload = response.json()
    providers = {
        (item["provider"], item["runtime_type"]): item for item in payload["providers"]
    }
    assert providers[("cloud_docker", "docker")] == {
        "provider": "cloud_docker",
        "runtime_type": "docker",
        "total": 1,
        "online": 1,
        "offline": 0,
        "degraded": 0,
        "running": 1,
        "capacity_slots": 2,
        "active_runs": 1,
        "utilization": 0.5,
    }
    assert providers[("self_hosted", "self_hosted")]["degraded"] == 1
    assert providers[("self_hosted", "self_hosted")]["capacity_slots"] == 3
    assert providers[("self_hosted", "self_hosted")]["active_runs"] == 1
    worker_types = {item["worker_type"]: item for item in payload["worker_types"]}
    assert worker_types["cloud"]["available_slots"] == 4
    assert worker_types["self_hosted"]["workers_draining"] == 1
    assert worker_types["self_hosted"]["running_jobs"] == 1
    assert worker_types["self_hosted"]["utilization"] == 0.5
    assert payload["runtime_spaces"][0]["quotas"][0]["quota_key"] == "docker_runtimes"
    assert other_response.status_code == 200
    assert other_response.json()["providers"][0]["total"] == 1
    assert other_response.json()["runtime_spaces"] == []


def test_operations_worker_lifecycle_reports_backlog_failure_and_latency() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace_with_role(
        session,
        email="other-worker-lifecycle@example.com",
        slug="other-worker-lifecycle",
    )
    keys = RedisKeyBuilder("chaincloud")
    redis.rpush(
        keys.queue("agent_runs"),
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=uuid4(),
            idempotency_key="cloud-worker-lifecycle",
            routing={"worker_types": ["cloud"]},
            created_at=datetime.now(UTC) - timedelta(seconds=90),
        ).model_dump_json(),
    )
    redis.rpush(
        keys.queue("agent_runs"),
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=uuid4(),
            idempotency_key="unrouted-worker-lifecycle",
            created_at=datetime.now(UTC) - timedelta(seconds=30),
        ).model_dump_json(),
    )
    redis.rpush(
        keys.queue("agent_runs"),
        JobPayload(
            workspace_id=other_workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=uuid4(),
            idempotency_key="other-worker-lifecycle",
            routing={"worker_types": ["cloud"]},
            created_at=datetime.now(UTC) - timedelta(seconds=600),
        ).model_dump_json(),
    )
    worker = WorkerNode(
        worker_id="worker-lifecycle-cloud",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 3},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    running_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-lifecycle-cloud",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    completed_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-lifecycle-cloud",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="completed",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC) - timedelta(seconds=80),
        finished_at=datetime.now(UTC) - timedelta(seconds=20),
    )
    failed_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-lifecycle-cloud",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="failed",
        attempt=1,
        lease_metadata={},
        started_at=datetime.now(UTC) - timedelta(seconds=70),
        finished_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    retrying_lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-lifecycle-cloud",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="retrying",
        attempt=1,
        lease_metadata={},
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        finished_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    other_lease = WorkerLease(
        workspace_id=other_workspace.id,
        worker_id="worker-lifecycle-cloud",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="failed",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add_all(
        [
            worker,
            running_lease,
            completed_lease,
            failed_lease,
            retrying_lease,
            other_lease,
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/worker-lifecycle",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    worker_types = {item["worker_type"]: item for item in payload["worker_types"]}
    assert worker_types["cloud"]["queued_jobs"] == 1
    assert worker_types["cloud"]["running_jobs"] == 1
    assert worker_types["cloud"]["completed_jobs"] == 1
    assert worker_types["cloud"]["failed_jobs"] == 1
    assert worker_types["cloud"]["retried_jobs"] == 1
    assert worker_types["cloud"]["failure_rate"] == 0.5
    assert worker_types["cloud"]["average_duration_seconds"] == 60
    assert worker_types["cloud"]["oldest_queued_age_seconds"] >= 80
    assert worker_types["cloud"]["oldest_running_age_seconds"] >= 110
    assert worker_types["unrouted"]["queued_jobs"] == 1
    assert "other-worker-lifecycle" not in str(payload)


def test_operations_control_plane_summarizes_capacity_and_health_issues() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    workspace.settings = {"scheduler": {"paused": True, "pause_reason": "maintenance"}}
    keys = RedisKeyBuilder("chaincloud")
    queued_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="old-control-plane-job",
        priority=9,
        created_at=datetime.now(UTC) - timedelta(seconds=400),
    )
    redis.rpush(keys.queue("agent_runs"), queued_job.model_dump_json())

    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Control Space",
        scope="workspace",
        status="paused",
    )
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="local",
        status="active",
        connection_status="degraded",
        capabilities={"max_concurrent_jobs": 1},
    )
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Blocked",
        status="queued",
        priority=9,
    )
    session.add_all([runtime, blocked_task])
    session.flush()
    self_hosted_worker = SelfHostedWorker(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        name="local-worker",
        machine_id="machine-control",
        version="2026.05",
        status="degraded",
        capabilities={},
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=900),
    )
    session.add(self_hosted_worker)
    session.flush()
    server = McpServer(
        workspace_id=workspace.id,
        name="tools",
        server_type="stdio",
        connection={},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=runtime.id,
        status="waiting_runtime",
    )
    session.add_all([server, run])
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        title="Blocked step",
        status="queued",
        order_index=0,
        runtime_space_id=runtime_space.id,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "runtime_space_paused",
        },
    )
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=1,
        reserved_value=1,
        unit="count",
    )
    worker = WorkerNode(
        worker_id="worker-full",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 1},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-full",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=queued_job.resource_id,
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    approval = Approval(
        workspace_id=workspace.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={"tool_name": "deploy"},
        status="pending",
        created_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    mcp_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={},
        status="failed",
    )
    session.add_all([blocked_step, quota, worker, lease, approval, mcp_job])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/control-plane?window_seconds=600",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    issue_codes = {issue["code"] for issue in payload["issues"]}
    assert payload["health"] == "critical"
    assert payload["queue"]["queued"] == 1
    assert payload["queue"]["oldest_age_seconds"] >= 390
    assert payload["worker_capacity"]["available_slots"] == 0
    assert payload["scheduler"]["backlog"]["blocked_steps"] == 1
    assert payload["outcomes"]["approvals"]["high_risk_pending"] == 1
    assert payload["mcp_jobs"]["failed"] == 1
    assert payload["self_hosted_machines"]["degraded"] == 1
    assert payload["self_hosted_machines"]["stale"] == 1
    assert {
        "queue_latency_high",
        "worker_capacity_exhausted",
        "scheduler_paused",
        "runtime_spaces_paused",
        "runtime_space_saturated",
        "runtime_provider_degraded",
        "scheduler_blocked_steps",
        "high_risk_approval_backlog",
        "mcp_jobs_failed",
        "self_hosted_machines_unhealthy",
        "self_hosted_heartbeat_stale",
    } <= issue_codes


def test_operations_mcp_jobs_reports_self_hosted_tool_queue() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace_with_role(
        session,
        email="other-mcp-jobs@example.com",
        slug="other-mcp-jobs",
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="local",
    )
    other_runtime = WorkspaceRuntime(
        workspace_id=other_workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="other",
    )
    session.add_all([runtime, other_runtime])
    session.flush()
    server = McpServer(
        workspace_id=workspace.id,
        name="tools",
        server_type="stdio",
        connection={},
    )
    other_server = McpServer(
        workspace_id=other_workspace.id,
        name="other-tools",
        server_type="stdio",
        connection={},
    )
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime.id, status="waiting_runtime")
    other_run = AgentRun(
        workspace_id=other_workspace.id,
        runtime_id=other_runtime.id,
        status="waiting_runtime",
    )
    session.add_all([server, other_server, run, other_run])
    session.flush()
    old_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={},
        status="queued",
        created_at=datetime.now(UTC) - timedelta(seconds=90),
    )
    claimed_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={},
        status="claimed",
    )
    failed_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="search_web",
        request_payload={},
        status="failed",
    )
    other_job = SelfHostedMcpJob(
        workspace_id=other_workspace.id,
        workspace_runtime_id=other_runtime.id,
        agent_run_id=other_run.id,
        mcp_server_id=other_server.id,
        tool_name="generate_image",
        request_payload={},
        status="queued",
    )
    session.add_all([old_job, claimed_job, failed_job, other_job])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/mcp-jobs",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["queued"] == 1
    assert payload["claimed"] == 1
    assert payload["failed"] == 1
    assert payload["oldest_queued_age_seconds"] >= 80
    assert {item["status"]: item["count"] for item in payload["statuses"]} == {
        "claimed": 1,
        "failed": 1,
        "queued": 1,
    }
    tools = {item["tool_name"]: item for item in payload["tools"]}
    assert tools["generate_image"]["total"] == 2
    assert tools["generate_image"]["queued"] == 1
    assert tools["search_web"]["failed"] == 1


def test_operations_self_hosted_machines_reports_trust_and_workload() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace_with_role(
        session,
        email="other-self-hosted-machines@example.com",
        slug="other-self-hosted-machines",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Local Team Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    active_runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="active-local",
        status="active",
        connection_status="online",
        capabilities={"max_concurrent_jobs": 2},
    )
    degraded_runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="degraded-local",
        status="active",
        connection_status="degraded",
    )
    other_runtime = WorkspaceRuntime(
        workspace_id=other_workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="other-local",
        status="active",
        connection_status="online",
    )
    session.add_all([active_runtime, degraded_runtime, other_runtime])
    session.flush()
    active_worker = SelfHostedWorker(
        workspace_id=workspace.id,
        workspace_runtime_id=active_runtime.id,
        name="active-worker",
        machine_id="machine-active",
        version="2026.05",
        status="online",
        capabilities={
            "allowed_tools": ["generate_image"],
            "supported_models": ["gpt-5.4"],
            "supported_runtimes": ["self_hosted"],
            "max_concurrent_jobs": 2,
            "max_concurrent_mcp_jobs": 1,
            "token": "machine-token",
            "headers": {"authorization": "Bearer hidden"},
        },
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    degraded_worker = SelfHostedWorker(
        workspace_id=workspace.id,
        workspace_runtime_id=degraded_runtime.id,
        name="degraded-worker",
        machine_id="machine-degraded",
        version="2026.05",
        status="degraded",
        capabilities={},
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=1_200),
    )
    other_worker = SelfHostedWorker(
        workspace_id=other_workspace.id,
        workspace_runtime_id=other_runtime.id,
        name="other-worker",
        machine_id="machine-other",
        version="2026.05",
        status="online",
        capabilities={},
        last_heartbeat_at=datetime.now(UTC),
    )
    active_credential = RuntimeCredential(
        workspace_id=workspace.id,
        workspace_runtime_id=active_runtime.id,
        token_hash="active-token",
        status="active",
    )
    degraded_credential = RuntimeCredential(
        workspace_id=workspace.id,
        workspace_runtime_id=degraded_runtime.id,
        token_hash="degraded-token",
        status="active",
    )
    session.add_all(
        [active_worker, degraded_worker, other_worker, active_credential, degraded_credential]
    )
    session.flush()
    server = McpServer(
        workspace_id=workspace.id,
        name="tools",
        server_type="stdio",
        connection={},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=active_runtime.id,
        status="running",
    )
    queued_run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=active_runtime.id,
        status="waiting_runtime",
    )
    session.add_all([server, run, queued_run])
    session.flush()
    session.add_all(
        [
            SelfHostedJobClaim(
                workspace_id=workspace.id,
                worker_id=active_worker.id,
                agent_run_id=run.id,
                status="claimed",
                claimed_at=datetime.now(UTC),
            ),
            SelfHostedMcpJob(
                workspace_id=workspace.id,
                workspace_runtime_id=active_runtime.id,
                worker_id=active_worker.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                request_payload={},
                status="claimed",
            ),
            SelfHostedMcpJob(
                workspace_id=workspace.id,
                workspace_runtime_id=active_runtime.id,
                agent_run_id=queued_run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                request_payload={},
                status="queued",
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/self-hosted-machines"
        "?stale_after_seconds=600",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["active"] == 1
    assert payload["degraded"] == 1
    assert payload["quarantined"] == 0
    assert payload["stale"] == 1
    assert payload["active_job_claims"] == 1
    assert payload["active_mcp_jobs"] == 1
    assert payload["queued_mcp_jobs"] == 1
    by_machine = {item["machine_id"]: item for item in payload["items"]}
    assert set(by_machine) == {"machine-active", "machine-degraded"}
    assert by_machine["machine-active"]["runtime_space_id"] == str(runtime_space.id)
    assert by_machine["machine-active"]["active_job_claims"] == 1
    assert by_machine["machine-active"]["active_mcp_jobs"] == 1
    assert by_machine["machine-active"]["queued_mcp_jobs"] == 1
    assert by_machine["machine-active"]["policy_summary"]["allowed_tools"] == ["generate_image"]
    assert by_machine["machine-active"]["capabilities"]["token"] == "[redacted]"
    assert by_machine["machine-active"]["capabilities"]["headers"] == "[redacted]"
    assert "machine-token" not in str(payload)
    assert "Bearer hidden" not in str(payload)
    assert by_machine["machine-active"]["warning_code"] is None
    assert by_machine["machine-active"]["remediation_actions"] == []
    assert by_machine["machine-degraded"]["stale"] is True
    assert by_machine["machine-degraded"]["warning_code"] == "machine_degraded"
    degraded_actions = {
        item["code"]: item for item in by_machine["machine-degraded"]["remediation_actions"]
    }
    assert set(degraded_actions) == {
        "check_runner_heartbeat",
        "restart_runner",
        "review_machine_policy",
    }
    assert degraded_actions["restart_runner"]["severity"] == "warning"


def test_operations_scheduler_reports_backlog_and_fairness_inputs() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    workspace.settings = {
        "scheduler": {
            "max_active_runs": 2,
            "max_running_tasks": 1,
            "max_runs_to_start_per_tick": 1,
            "max_steps_per_task_per_tick": 1,
            "starvation_boost_after_seconds": 60,
            "resource_limits": {"cpu": 4},
            "paused": True,
            "pause_reason": "maintenance",
        }
    }
    queued_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="High priority",
        status="queued",
        priority=10,
    )
    running_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Running",
        status="running",
        priority=5,
    )
    waiting_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Waiting",
        status="waiting_approval",
        priority=3,
    )
    session.add_all([queued_task, running_task, waiting_task])
    session.flush()
    queued_step = TaskStep(
        workspace_id=workspace.id,
        task_id=queued_task.id,
        title="Queued step",
        status="queued",
        order_index=0,
    )
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=queued_task.id,
        title="Blocked step",
        status="queued",
        order_index=1,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "workspace_run_quota_exceeded",
        },
    )
    running_step = TaskStep(
        workspace_id=workspace.id,
        task_id=running_task.id,
        title="Running step",
        status="running",
        order_index=0,
    )
    session.add_all([queued_step, blocked_step, running_step])
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=running_task.id,
            status="running",
            input={},
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler",
        headers=_headers(owner.id),
    )
    blocked_steps = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps"
        "?code=workspace_quota_exceeded",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert blocked_steps.status_code == 200
    payload = response.json()
    assert payload["backlog"]["queued_steps"] == 2
    assert payload["backlog"]["running_steps"] == 1
    assert payload["backlog"]["waiting_approval_tasks"] == 1
    assert payload["backlog"]["blocked_steps"] == 1
    assert payload["backlog"]["active_runs"] == 1
    assert payload["backlog"]["highest_priority"] == 10
    assert payload["priority_buckets"][0] == {
        "priority": 10,
        "queued_steps": 2,
        "running_steps": 0,
        "blocked_steps": 1,
    }
    assert payload["blocked_reasons"] == [
        {
            "reason": "workspace_run_quota_exceeded",
            "code": "workspace_quota_exceeded",
            "message": "Workspace quota is exhausted.",
            "resource_key": None,
            "count": 1,
        }
    ]
    blocked_payload = blocked_steps.json()
    assert blocked_payload["total"] == 1
    assert blocked_payload["items"][0]["task_step_id"] == str(blocked_step.id)
    assert blocked_payload["items"][0]["task_title"] == "High priority"
    assert blocked_payload["items"][0]["step_title"] == "Blocked step"
    assert blocked_payload["items"][0]["reason"] == "workspace_run_quota_exceeded"
    assert blocked_payload["items"][0]["code"] == "workspace_quota_exceeded"
    assert blocked_payload["items"][0]["message"] == "Workspace quota is exhausted."
    assert payload["policy"] == {
        "paused": True,
        "pause_reason": "maintenance",
        "max_active_runs": 2,
        "max_running_tasks": 1,
        "max_runs_to_start_per_tick": 1,
        "max_steps_per_task_per_tick": 1,
        "starvation_boost_after_seconds": 60,
        "resource_limits": {"cpu": 4.0},
    }

    unblocked = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps/unblock",
        headers=_headers(owner.id),
        json={"code": "workspace_quota_exceeded", "limit": 10},
    )
    bad_unblock = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps/unblock",
        headers=_headers(owner.id),
        json={},
    )

    session.refresh(blocked_step)
    audit_event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "scheduler.blocked_steps_unblocked",
        )
    )
    assert unblocked.status_code == 200
    assert unblocked.json()["unblocked_steps"] == 1
    assert bad_unblock.status_code == 400
    assert "scheduling_status" not in blocked_step.dependencies
    assert "blocked_reason" not in blocked_step.dependencies
    assert audit_event is not None
    assert audit_event.audit_metadata["code"] == "workspace_quota_exceeded"
    assert audit_event.audit_metadata["unblocked_steps"] == 1


def test_operations_scheduler_pause_and_resume_control_policy() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    viewer = User(email="viewer@example.com", display_name="Viewer")
    session.add(viewer)
    session.add(WorkspaceMember(workspace=workspace, user=viewer, role="viewer"))
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Queued work",
        status="queued",
        priority=3,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Queued step",
        status="queued",
        order_index=0,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "maintenance",
            "priority_score": 10,
        },
    )
    session.add(step)
    session.commit()

    paused = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/pause",
        headers=_headers(owner.id),
        json={"reason": "maintenance"},
    )
    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/resume",
        headers=_headers(viewer.id),
    )
    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/resume",
        headers=_headers(owner.id),
    )

    session.refresh(workspace)
    session.refresh(step)
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()

    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert paused.json()["pause_reason"] == "maintenance"
    assert denied.status_code == 403
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    assert resumed.json()["cleared_blocked_steps"] == 1
    assert workspace.settings["scheduler"]["paused"] is False
    assert "pause_reason" not in workspace.settings["scheduler"]
    assert step.dependencies == {}
    assert [event.action for event in events] == [
        "workspace.scheduler_paused",
        "workspace.scheduler_resumed",
    ]
    assert events[1].audit_metadata["cleared_blocked_steps"] == 1


def test_operations_outcomes_reports_failure_rate_and_approval_backlog() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    now = datetime.now(UTC)
    completed_run = AgentRun(
        workspace_id=workspace.id,
        status="completed",
        input={},
        updated_at=now - timedelta(seconds=120),
    )
    failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "RuntimeError", "message": "model failed"},
        updated_at=now - timedelta(seconds=90),
    )
    cancelled_run = AgentRun(
        workspace_id=workspace.id,
        status="cancelled",
        input={},
        updated_at=now - timedelta(seconds=60),
    )
    old_failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "OldError"},
        updated_at=now - timedelta(seconds=10_000),
    )
    approval = Approval(
        workspace_id=workspace.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={"tool_name": "deploy"},
        status="pending",
        created_at=now - timedelta(seconds=300),
    )
    low_risk_approval = Approval(
        workspace_id=workspace.id,
        approval_type="runtime.command",
        risk_level="medium",
        payload={"command": "ls"},
        status="pending",
        created_at=now - timedelta(seconds=120),
    )
    decided_approval = Approval(
        workspace_id=workspace.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={},
        status="approved",
        created_at=now - timedelta(seconds=400),
    )
    session.add_all(
        [
            completed_run,
            failed_run,
            cancelled_run,
            old_failed_run,
            approval,
            low_risk_approval,
            decided_approval,
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/outcomes?window_seconds=600",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"] == {
        "window_seconds": 600,
        "total_runs": 3,
        "completed_runs": 1,
        "failed_runs": 1,
        "cancelled_runs": 1,
        "failure_rate": 0.3333,
        "failure_reasons": [{"code": "RuntimeError", "count": 1}],
    }
    assert payload["approvals"]["pending"] == 2
    assert payload["approvals"]["high_risk_pending"] == 1
    assert payload["approvals"]["oldest_pending_age_seconds"] >= 290
    assert payload["approvals"]["pending_by_type"] == {
        "mcp.tool": 1,
        "runtime.command": 1,
    }


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
        lease_metadata={"task": "owned", "token": "worker-lease-token"},
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
    assert response.json()["items"][0]["lease_metadata"] == {
        "task": "owned",
        "token": "[redacted]",
    }
    assert other_response.status_code == 200
    assert other_response.json()["total"] == 1
    assert other_response.json()["items"][0]["job_id"] == str(other_lease.job_id)


def test_operations_lists_runtime_leases_by_workspace() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    other_user, other_workspace = _seed_workspace_with_role(
        session,
        email="other-runtime-lease@example.com",
        slug="other-runtime-lease",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Runtime Space",
        scope="workspace",
    )
    other_runtime_space = RuntimeSpace(
        workspace_id=other_workspace.id,
        name="Other Runtime Space",
        scope="workspace",
    )
    session.add_all([runtime_space, other_runtime_space])
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="owned-runtime",
        docker_container_id="container-owned",
    )
    other_runtime = WorkspaceRuntime(
        workspace_id=other_workspace.id,
        runtime_space_id=other_runtime_space.id,
        name="other-runtime",
        docker_container_id="container-other",
    )
    session.add_all([runtime, other_runtime])
    session.flush()
    lease = RuntimeLease(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        runtime_space_id=runtime_space.id,
        docker_container_id="container-owned",
        status="running",
        lease_metadata={"purpose": "owned", "docker_container_id": "container-owned"},
        acquired_at=datetime.now(UTC),
    )
    other_lease = RuntimeLease(
        workspace_id=other_workspace.id,
        workspace_runtime_id=other_runtime.id,
        runtime_space_id=other_runtime_space.id,
        docker_container_id="container-other",
        status="running",
        lease_metadata={"purpose": "other"},
        acquired_at=datetime.now(UTC),
    )
    session.add_all([lease, other_lease])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-leases"
        f"?status=running&runtime_space_id={runtime_space.id}",
        headers=_headers(owner.id),
    )
    other_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/runtime-leases",
        headers=_headers(other_user.id),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["workspace_runtime_id"] == str(runtime.id)
    assert "docker_container_id" not in response.json()["items"][0]
    assert response.json()["items"][0]["has_docker_container"] is True
    assert response.json()["items"][0]["lease_metadata"] == {
        "purpose": "owned",
        "docker_container_id": "[redacted]",
    }
    assert other_response.status_code == 200
    assert other_response.json()["total"] == 1
    assert other_response.json()["items"][0]["workspace_runtime_id"] == str(other_runtime.id)


def test_operations_runtime_leases_reject_foreign_runtime_space_filter() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace_with_role(
        session,
        email="foreign-runtime-lease@example.com",
        slug="foreign-runtime-lease",
    )
    runtime_space = RuntimeSpace(
        workspace_id=other_workspace.id,
        name="Foreign Runtime Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=other_workspace.id,
        runtime_space_id=runtime_space.id,
        name="foreign-runtime",
        docker_container_id="foreign-container",
    )
    session.add(runtime)
    session.flush()
    session.add(
        RuntimeLease(
            workspace_id=other_workspace.id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime_space.id,
            docker_container_id="foreign-container",
            status="running",
            lease_metadata={"secret": "foreign"},
            acquired_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-leases"
        f"?status=running&runtime_space_id={runtime_space.id}",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


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


def test_workspace_security_event_response_redacts_sensitive_metadata() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    client, session = _client(redis)
    owner, workspace = _seed_workspace(session)
    session.add(
        SecurityEvent(
            workspace_id=workspace.id,
            user_id=owner.id,
            action="mcp_tool.blocked",
            outcome="denied",
            severity="warning",
            path="/api/v1/workspaces/x/mcp",
            method="POST",
            reason="policy",
            event_metadata={
                "authorization": "Bearer secret-token",
                "headers": {"x-api-key": "sk-secret"},
                "nested": {"base_url": "https://router.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/security-events"
        "?action=mcp_tool.blocked",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "authorization": "[redacted]",
        "headers": "[redacted]",
        "nested": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "secret-token" not in str(metadata)
    assert "router.example.test/private" not in str(metadata)


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
