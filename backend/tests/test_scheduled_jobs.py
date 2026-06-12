from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
from backend.app.model_providers import service as model_provider_service_module
from backend.app.model_providers.health import (
    ModelProviderHealthCheck,
    ModelProviderHealthCheckResult,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.scheduled_jobs.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from backend.app.scheduled_jobs.service import WorkspaceScheduledJobService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workers.runner import WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_scheduled_jobs_api_is_workspace_scoped_audited_and_redacted() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other",
    )
    run_at = datetime.now(UTC) + timedelta(hours=1)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs",
        headers=_headers(owner.id),
        json={
            "name": "Daily maintenance",
            "schedule": {"type": "one_shot", "run_at": run_at.isoformat()},
            "action_type": "record_due_action",
            "metadata": {"token": "secret-token", "visible": "ok"},
        },
    )
    foreign = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs",
        headers=_headers(other_owner.id),
        json={
            "name": "Forbidden",
            "schedule": {"type": "hourly", "minute": 0},
            "action_type": "record_due_action",
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs",
        headers=_headers(owner.id),
    )
    foreign_list = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/scheduled-jobs",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["metadata"] == {"token": "[redacted]", "visible": "ok"}
    assert "secret-token" not in str(body)
    assert foreign.status_code == 403
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == body["id"]
    assert foreign_list.status_code == 403

    stored = session.get(WorkspaceScheduledJob, UUID(body["id"]))
    assert stored is not None
    assert stored.metadata_["token"] == "secret-token"

    pause = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs/{body['id']}/pause",
        headers=_headers(owner.id),
    )
    resume = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs/{body['id']}/resume",
        headers=_headers(owner.id),
    )

    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"
    assert pause.json()["paused_at"] is not None
    assert resume.status_code == 200
    assert resume.json()["status"] == "active"
    assert resume.json()["next_run_at"] is not None

    actions = {
        event.action
        for event in session.scalars(
            select(AuditEvent).where(AuditEvent.workspace_id == workspace.id)
        )
    }
    assert {
        "workspace.scheduled_job.created",
        "workspace.scheduled_job.paused",
        "workspace.scheduled_job.resumed",
    } <= actions


def test_scheduled_job_maintenance_enqueues_or_records_due_actions() -> None:
    _, session, queue = _client()
    owner, workspace = _seed_workspace(session, email="due@example.com", slug="due")
    resource_id = uuid4()
    now = datetime.now(UTC)
    enqueued = WorkspaceScheduledJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Queue run",
        schedule_type="hourly",
        schedule_config={"minute": now.minute},
        status="active",
        action_type="queue_job",
        job_type="task.plan",
        resource_id=resource_id,
        routing={"token": "routing-secret"},
        priority=5,
        max_attempts=2,
        metadata_={"api_key": "metadata-secret"},
        next_run_at=now - timedelta(minutes=1),
    )
    recorded = WorkspaceScheduledJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Record only",
        schedule_type="one_shot",
        schedule_config={"run_at": (now - timedelta(minutes=1)).isoformat()},
        status="active",
        action_type="record_due_action",
        routing={},
        metadata_={"token": "record-secret"},
        next_run_at=now - timedelta(minutes=1),
    )
    session.add_all([enqueued, recorded])
    session.commit()

    summary = WorkspaceScheduledJobService(session).enqueue_due(queue=queue, now=now)

    assert summary.enqueued == 1
    assert summary.recorded == 1
    assert summary.skipped == 0
    assert summary.enqueued_by_job_type == {"task.plan": 1}
    assert summary.recorded_by_job_type == {"record_due_action": 1}
    assert summary.skipped_by_job_type == {}
    queued_job = queue.dequeue()
    assert queued_job is not None
    assert queued_job.workspace_id == workspace.id
    assert queued_job.job_type == "task.plan"
    assert queued_job.resource_id == resource_id
    assert queued_job.priority == 5
    assert queued_job.max_attempts == 2

    session.refresh(enqueued)
    session.refresh(recorded)
    assert enqueued.status == "active"
    assert enqueued.next_run_at is not None
    assert enqueued.next_run_at > now.replace(tzinfo=None)
    assert recorded.status == "completed"
    assert recorded.next_run_at is None

    events = session.scalars(
        select(WorkspaceScheduledJobEvent)
        .where(WorkspaceScheduledJobEvent.workspace_id == workspace.id)
        .order_by(WorkspaceScheduledJobEvent.created_at.asc())
    ).all()
    assert {event.status for event in events} == {"enqueued", "recorded"}
    enqueued_event = next(event for event in events if event.status == "enqueued")
    assert enqueued_event.metadata_["metadata"]["api_key"] == "metadata-secret"
    audit = session.scalars(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.scheduled_job.due",
        )
    ).all()
    assert len(audit) == 2
    assert {event.audit_metadata["status"] for event in audit} == {"enqueued", "recorded"}


def test_worker_maintenance_scans_due_scheduled_jobs() -> None:
    session_factory = _session_factory()
    queue = _queue()
    with session_factory() as session:
        owner, workspace = _seed_workspace(session, email="worker-due@example.com", slug="worker")
        session.add(
            WorkspaceScheduledJob(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                name="Worker maintenance due",
                schedule_type="one_shot",
                schedule_config={
                    "run_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
                },
                status="active",
                action_type="record_due_action",
                routing={},
                metadata_={"token": "worker-secret"},
                next_run_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        session.commit()
        workspace_id = workspace.id

    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="scheduled-jobs-worker", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()

    assert summary.scheduled_job_actions_recorded == 1
    assert summary.scheduled_job_actions_enqueued == 0
    assert summary.scheduled_job_actions_recorded_by_job_type == {
        "record_due_action": 1
    }
    assert summary.scheduled_job_actions_enqueued_by_job_type == {}
    with session_factory() as session:
        event = session.scalar(
            select(WorkspaceScheduledJobEvent).where(
                WorkspaceScheduledJobEvent.workspace_id == workspace_id
            )
        )
        assert event is not None
        assert event.status == "recorded"
        assert event.metadata_["metadata"]["token"] == "worker-secret"


def test_scheduled_model_provider_health_check_is_scoped_and_redacted() -> None:
    client, session, queue = _client()
    owner, workspace = _seed_workspace(session, email="health-owner@example.com", slug="health")
    other_owner, other_workspace = _seed_workspace(
        session,
        email="health-other@example.com",
        slug="health-other",
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Claude Gateway",
        provider="openai-compatible",
        api_key="sk-scheduled-provider",
        default_model="claude-opus-4-6",
        base_url="https://dash.ovload.com/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    run_at = datetime.now(UTC) - timedelta(minutes=1)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs",
        headers=_headers(owner.id),
        json={
            "name": "Claude health",
            "schedule": {"type": "one_shot", "run_at": run_at.isoformat()},
            "action_type": "queue_job",
            "job_type": "model_provider.health_check",
            "resource_id": str(credential.id),
            "routing": {"probes": ["models"], "timeout_seconds": 5},
            "metadata": {"token": "secret-schedule", "purpose": "provider-health"},
        },
    )
    invalid_routing = client.post(
        f"/api/v1/workspaces/{workspace.id}/scheduled-jobs",
        headers=_headers(owner.id),
        json={
            "name": "Bad health",
            "schedule": {"type": "one_shot", "run_at": run_at.isoformat()},
            "action_type": "queue_job",
            "job_type": "model_provider.health_check",
            "resource_id": str(credential.id),
            "routing": {"api_key": "sk-nope"},
        },
    )
    cross_workspace = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/scheduled-jobs",
        headers=_headers(other_owner.id),
        json={
            "name": "Foreign health",
            "schedule": {"type": "one_shot", "run_at": run_at.isoformat()},
            "action_type": "queue_job",
            "job_type": "model_provider.health_check",
            "resource_id": str(credential.id),
        },
    )

    assert created.status_code == 201
    assert created.json()["job_type"] == "model_provider.health_check"
    assert created.json()["routing"] == {"probes": ["models"], "timeout_seconds": 5}
    assert created.json()["metadata"] == {
        "token": "[redacted]",
        "purpose": "provider-health",
    }
    assert "secret-schedule" not in str(created.json())
    assert invalid_routing.status_code == 400
    assert cross_workspace.status_code == 400
    credentials = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
    )
    scheduled_health_check = credentials.json()["items"][0]["scheduled_health_check"]
    assert credentials.status_code == 200
    assert scheduled_health_check["configured"] is True
    assert scheduled_health_check["active_count"] == 1
    assert scheduled_health_check["paused_count"] == 0
    assert scheduled_health_check["jobs"][0]["name"] == "Claude health"
    assert scheduled_health_check["jobs"][0]["routing"] == {
        "probes": ["models"],
        "timeout_seconds": 5,
    }
    assert "secret-schedule" not in str(credentials.json())

    summary = WorkspaceScheduledJobService(session).enqueue_due(queue=queue, now=datetime.now(UTC))
    queued = queue.dequeue()

    assert summary.enqueued == 1
    assert summary.enqueued_by_job_type == {
        JobType.MODEL_PROVIDER_HEALTH_CHECK.value: 1
    }
    assert queued is not None
    assert queued.job_type == JobType.MODEL_PROVIDER_HEALTH_CHECK
    assert queued.resource_id == credential.id
    assert queued.routing == {"probes": ["models"], "timeout_seconds": 5}


def test_worker_runs_model_provider_health_check_job(monkeypatch) -> None:
    session_factory = _session_factory()
    queue = _queue()
    captured: dict[str, object] = {}

    async def fake_probe(target, *, probes, timeout_seconds):
        captured["provider"] = target.provider
        captured["model"] = target.model
        captured["api_key"] = target.api_key
        captured["base_url"] = target.base_url
        captured["probes"] = probes
        captured["timeout_seconds"] = timeout_seconds
        return ModelProviderHealthCheckResult(
            status="healthy",
            checks=(
                ModelProviderHealthCheck(
                    name="models",
                    status="passed",
                    metadata={"model_count": 4, "model_present": True},
                ),
            ),
        )

    monkeypatch.setattr(model_provider_service_module, "probe_model_provider", fake_probe)
    with session_factory() as session:
        owner, workspace = _seed_workspace(
            session,
            email="worker-health@example.com",
            slug="worker-health",
        )
        credential = ModelProviderCredentialService(
            session,
            SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            name="Claude Gateway",
            provider="openai-compatible",
            api_key="sk-worker-health",
            default_model="claude-opus-4-6",
            base_url="https://dash.ovload.com/v1",
            is_default=False,
            budget_metadata={"model_api": "chat_completions"},
        )
        queue.enqueue(
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.MODEL_PROVIDER_HEALTH_CHECK,
                resource_id=credential.id,
                requested_by_user_id=owner.id,
                routing={"probes": ["models"], "timeout_seconds": 4},
                idempotency_key=f"model-provider-health:{credential.id}",
            )
        )
        workspace_id = workspace.id
        credential_id = credential.id

    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="provider-health-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            credential_encryption_secret="unit-test-secret",
            credential_encryption_key_id="test-key",
        ),
    )

    assert runner.run_once() is True
    assert captured == {
        "provider": "openai-compatible",
        "model": "claude-opus-4-6",
        "api_key": "sk-worker-health",
        "base_url": "https://dash.ovload.com/v1",
        "probes": ("models",),
        "timeout_seconds": 4,
    }
    with session_factory() as session:
        credential = session.get(ModelProviderCredential, credential_id)
        assert credential is not None
        assert credential.health_status == "healthy"
        assert credential.failure_count == 0
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "model_provider_credential.health_checked",
            )
        )
        assert audit is not None
        assert audit.audit_metadata["checks"][0]["status"] == "passed"
        assert "sk-worker-health" not in str(audit.audit_metadata)


def _client() -> tuple[TestClient, Session, RedisQueue]:
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
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url="sqlite+pysqlite:///:memory:",
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
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session, queue


def _session_factory() -> sessionmaker[Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )


def _seed_workspace(session: Session, *, email: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
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
