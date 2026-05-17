from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
        f"/api/v1/workspaces/{workspace.id}/tasks?status=draft",
        headers=_headers(owner.id),
    )
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1


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
