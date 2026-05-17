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
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
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

    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
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
        json={"worker_id": "worker-1", "details": {"pid": 123}},
    )
    assert heartbeat.status_code == 200

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
    user = User(email="owner@example.com", display_name="owner")
    workspace = Workspace(owner=user, name="Owner", slug="owner", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
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
