from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
from backend.app.notifications.models import WorkspaceNotification
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_notifications_are_workspace_scoped_counted_and_redacted() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-owner@example.com",
        slug="other-space",
    )
    now = datetime(2026, 6, 6, 1, 0, tzinfo=UTC)

    unread = _add_notification(
        session,
        workspace=workspace,
        title="Runtime warning",
        notification_type="runtime",
        severity="warning",
        source_type="runtime",
        metadata={"token": "secret-token", "visible": "ok"},
        created_at=now,
    )
    _add_notification(
        session,
        workspace=workspace,
        title="Task completed",
        notification_type="task",
        severity="info",
        source_type="task",
        read_at=now + timedelta(minutes=1),
        created_at=now + timedelta(minutes=1),
    )
    _add_notification(
        session,
        workspace=workspace,
        title="Archived incident",
        notification_type="security",
        severity="critical",
        source_type="security",
        metadata={"api_key": "sk-hidden"},
        read_at=now + timedelta(minutes=2),
        archived_at=now + timedelta(minutes=3),
        created_at=now + timedelta(minutes=2),
    )
    _add_notification(
        session,
        workspace=other_workspace,
        title="Foreign secret must not leak",
        notification_type="runtime",
        severity="warning",
        source_type="runtime",
        metadata={"token": "foreign-token"},
        created_at=now + timedelta(minutes=4),
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications",
        headers=_headers(owner.id),
    )
    counts = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications/counts",
        headers=_headers(owner.id),
    )
    unread_only = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications?read=false",
        headers=_headers(owner.id),
    )
    with_archived = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications?include_archived=true",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][1]["id"] == str(unread.id)
    assert body["items"][1]["metadata"] == {"token": "[redacted]", "visible": "ok"}
    assert "Foreign secret must not leak" not in response.text
    assert "foreign-token" not in response.text

    stored = session.scalar(
        select(WorkspaceNotification).where(WorkspaceNotification.id == unread.id)
    )
    assert stored is not None
    assert stored.metadata_ == {"token": "secret-token", "visible": "ok"}

    assert counts.status_code == 200
    assert counts.json()["total_count"] == 3
    assert counts.json()["unread_count"] == 1
    assert counts.json()["read_count"] == 1
    assert counts.json()["archived_count"] == 1
    assert counts.json()["severity_counts"] == {"critical": 1, "info": 1, "warning": 1}
    assert counts.json()["type_counts"] == {"runtime": 1, "security": 1, "task": 1}
    assert counts.json()["source_type_counts"] == {"runtime": 1, "security": 1, "task": 1}
    assert unread_only.json()["total"] == 1
    assert with_archived.json()["total"] == 3
    assert forbidden.status_code == 403


def test_notifications_mark_read_archive_and_cross_workspace_ids_are_scoped() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="foreign-owner@example.com",
        slug="foreign-space",
    )
    visible = _add_notification(
        session,
        workspace=workspace,
        title="Needs attention",
        notification_type="task",
        severity="warning",
        source_type="task",
    )
    archived_unread = _add_notification(
        session,
        workspace=workspace,
        title="Archived but unread",
        notification_type="runtime",
        severity="info",
        source_type="runtime",
        archived_at=datetime(2026, 6, 6, 1, 0, tzinfo=UTC),
    )
    foreign = _add_notification(
        session,
        workspace=other_workspace,
        title="Foreign",
        notification_type="runtime",
        severity="warning",
        source_type="runtime",
    )

    bulk = client.post(
        f"/api/v1/workspaces/{workspace.id}/notifications/mark-read",
        headers=_headers(owner.id),
        json={"notification_ids": [str(visible.id), str(archived_unread.id), str(foreign.id)]},
    )
    archive = client.post(
        f"/api/v1/workspaces/{workspace.id}/notifications/{visible.id}/archive",
        headers=_headers(owner.id),
    )
    foreign_read = client.post(
        f"/api/v1/workspaces/{workspace.id}/notifications/{foreign.id}/read",
        headers=_headers(owner.id),
    )
    forbidden_archive = client.post(
        f"/api/v1/workspaces/{workspace.id}/notifications/{visible.id}/archive",
        headers=_headers(other_owner.id),
    )

    assert bulk.status_code == 200
    assert bulk.json() == {"workspace_id": str(workspace.id), "updated_count": 1}
    session.refresh(visible)
    session.refresh(archived_unread)
    session.refresh(foreign)
    assert visible.read_at is not None
    assert archived_unread.read_at is None
    assert foreign.read_at is None

    assert archive.status_code == 200
    assert archive.json()["read_at"] is not None
    assert archive.json()["archived_at"] is not None
    assert foreign_read.status_code == 404
    assert forbidden_archive.status_code == 403


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
    redis = fakeredis.FakeRedis(decode_responses=True)

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
    worker_queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder(app.state.settings.redis_key_prefix),
        queue_name=app.state.settings.worker_queue_name,
        blocking_timeout_seconds=0,
    )
    app.dependency_overrides[get_worker_queue] = lambda: worker_queue
    return TestClient(app), session


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _add_notification(
    session: Session,
    *,
    workspace: Workspace,
    title: str,
    notification_type: str,
    severity: str,
    source_type: str,
    metadata: dict[str, object] | None = None,
    read_at: datetime | None = None,
    archived_at: datetime | None = None,
    created_at: datetime | None = None,
) -> WorkspaceNotification:
    timestamp = created_at or datetime(2026, 6, 6, 1, 0, tzinfo=UTC)
    notification = WorkspaceNotification(
        workspace_id=workspace.id,
        title=title,
        body="",
        notification_type=notification_type,
        severity=severity,
        source_type=source_type,
        metadata_=metadata or {},
        read_at=read_at,
        archived_at=archived_at,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(notification)
    session.commit()
    return notification


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
