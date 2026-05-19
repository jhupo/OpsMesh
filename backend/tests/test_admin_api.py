from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"


def test_admin_api_requires_platform_admin_token() -> None:
    client, session = _client()
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
    client, session = _client()
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
    client, session = _client()
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


def _client() -> tuple[TestClient, Session]:
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
    return TestClient(app), session


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
