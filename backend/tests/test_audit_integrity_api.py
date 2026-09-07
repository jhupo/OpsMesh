from collections.abc import Generator
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.audit.models import AuditEvent, AuditIntegrityCheck
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "audit-integrity-api-token"


def test_audit_integrity_api_queues_worker_verification_and_reports_status() -> None:
    client, session, queue = _client()
    owner, workspace = _seed_workspace(session, "audit-integrity")
    other_owner, _ = _seed_workspace(session, "audit-integrity-other")
    AuditService(session).record_user_action(
        workspace_id=workspace.id,
        user_id=owner.id,
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
    )
    session.commit()

    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(owner.id),
    )
    queued = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity/verify",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(other_owner.id),
    )

    assert missing.status_code == 200
    assert missing.json() == {"status": "missing", "stale": True, "latest": None}
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert forbidden.status_code == 403

    job = queue.dequeue()
    assert job is not None
    assert job.job_type == JobType.AUDIT_INTEGRITY_CHECK
    assert job.workspace_id == workspace.id
    WorkerJobHandler(session).handle(job)
    session.commit()

    status = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(owner.id),
    )
    assert status.status_code == 200
    assert status.json()["status"] == "valid"
    assert status.json()["stale"] is False
    assert status.json()["latest"]["checked_events"] == 2
    assert (
        session.scalar(
            select(AuditIntegrityCheck).where(AuditIntegrityCheck.workspace_id == workspace.id)
        )
        is not None
    )
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "audit.integrity_verification_queued",
            )
        )
        is not None
    )


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
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
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
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session, queue


def _seed_workspace(session: Session, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name=slug)
    workspace = Workspace(owner=user, name=slug, slug=f"{slug}-{uuid4()}", settings={})
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
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
