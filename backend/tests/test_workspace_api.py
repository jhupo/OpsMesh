from collections.abc import Generator

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_workspace_and_resource_api_enforces_scope_and_roles() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer, other_workspace = _seed_workspace(
        session,
        role="viewer",
        email="viewer@example.com",
        slug="viewer-space",
    )

    response = client.get("/api/v1/workspaces", headers=_headers(owner.id))
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(workspace.id)

    forbidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/agents",
        headers=_headers(owner.id),
    )
    assert forbidden.status_code == 403

    created_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Researcher", "role": "researcher", "instructions": "Research well"},
    )
    assert created_agent.status_code == 201
    assert created_agent.json()["workspace_id"] == str(workspace.id)

    created_team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Research Team", "team_type": "research"},
    )
    assert created_team.status_code == 201

    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Q2 Market Analysis", "domain_type": "market_research"},
    )
    assert created_task.status_code == 201
    assert created_task.json()["created_by_user_id"] == str(owner.id)

    viewer_create = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(viewer.id),
        json={"title": "Should fail"},
    )
    assert viewer_create.status_code == 403

    tasks = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks?status=queued",
        headers=_headers(owner.id),
    )
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1

    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
    )
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"agent.created", "team.created", "task.created"} <= actions


def test_create_task_is_idempotent_within_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    headers = _headers(owner.id) | {"Idempotency-Key": "create-q2-task"}

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={"title": "Q2 Market Analysis"},
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={"title": "Q2 Market Analysis"},
    )

    tasks = session.scalars(select(Task).where(Task.workspace_id == workspace.id)).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.workspace_id == workspace.id)).all()
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
    )
    task_created_events = [
        item for item in audit.json()["items"] if item["action"] == "task.created"
    ]
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(tasks) == 1
    assert len(runs) == 1
    assert len(task_created_events) == 1


def test_task_idempotency_key_is_scoped_by_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", email="owner@example.com", slug="one")
    other, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="two",
    )
    key = "same-client-key"

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id) | {"Idempotency-Key": key},
        json={"title": "Owner task"},
    )
    second = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(other.id) | {"Idempotency-Key": key},
        json={"title": "Other task"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_create_workspace_assigns_owner_membership() -> None:
    client, session = _client()
    user = User(email="new-owner@example.com", display_name="New Owner")
    session.add(user)
    session.commit()

    response = client.post(
        "/api/v1/workspaces",
        headers=_headers(user.id),
        json={"name": "New AI Company", "slug": "new-ai-company"},
    )

    assert response.status_code == 201
    workspace_id = response.json()["id"]
    members = client.get(f"/api/v1/workspaces/{workspace_id}/members", headers=_headers(user.id))
    assert members.status_code == 200
    assert members.json()["items"][0]["role"] == "owner"


def test_duplicate_workspace_slug_returns_conflict_error() -> None:
    client, session = _client()
    user = User(email="owner@example.com", display_name="Owner")
    session.add(user)
    session.commit()
    headers = _headers(user.id)

    first = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme", "slug": "acme"},
    )
    duplicate = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme Again", "slug": "acme"},
    )
    after_conflict = client.get("/api/v1/workspaces", headers=headers)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert duplicate.json()["error"]["message"] == "Workspace slug already exists"
    assert after_conflict.status_code == 200
    assert after_conflict.json()["total"] == 1


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
