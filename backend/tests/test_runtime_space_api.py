from collections.abc import Generator
from uuid import UUID

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
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
)
from backend.app.teams.models import AgentTeam
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_runtime_space_api_lifecycle_and_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={
            "name": "Research runtime space",
            "scope": "workspace",
            "network_policy": {"mode": "none"},
            "quota_limits": {"active_runs": 2, "memory_mb": 2048},
        },
    )

    assert created.status_code == 201
    runtime_space_id = UUID(created.json()["id"])
    assert created.json()["workspace_id"] == str(workspace.id)
    assert created.json()["created_by_user_id"] == str(owner.id)
    assert created.json()["network_policy"] == {"mode": "none"}

    forbidden_read = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/runtime-spaces/{runtime_space_id}",
        headers=_headers(other_owner.id),
    )
    assert forbidden_read.status_code == 404

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
    )
    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}/events",
        headers=_headers(owner.id),
    )
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}",
        headers=_headers(owner.id),
        json={"status": "disabled", "quota_limits": {"active_runs": 1}},
    )
    reset = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}/reset",
        headers=_headers(owner.id),
    )

    quotas = session.scalars(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.runtime_space_id == runtime_space_id)
    ).all()
    bindings = session.scalars(
        select(RuntimeSpaceBinding).where(
            RuntimeSpaceBinding.runtime_space_id == runtime_space_id,
        )
    ).all()

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert updated.status_code == 200
    assert updated.json()["status"] == "disabled"
    assert reset.status_code == 200
    assert {quota.quota_key: quota.limit_value for quota in quotas} == {
        "active_runs": 1,
        "memory_mb": 2048,
    }
    assert {quota.quota_key: quota.status for quota in quotas} == {
        "active_runs": "active",
        "memory_mb": "disabled",
    }
    assert len(bindings) == 1
    assert bindings[0].target_type == "workspace"
    assert bindings[0].target_id == workspace.id


def test_team_runtime_space_flows_to_task_and_initial_run() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    runtime_space = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={"name": "Design team space", "scope": "workspace"},
    )
    runtime_space_id = UUID(runtime_space.json()["id"])

    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Design Team",
            "team_type": "design",
            "runtime_space_id": str(runtime_space_id),
        },
    )
    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Create landing mock", "agent_team_id": team.json()["id"]},
    )

    stored_team = session.get(AgentTeam, UUID(team.json()["id"]))
    stored_runs = session.scalars(
        select(AgentRun).where(AgentRun.workspace_id == workspace.id)
    ).all()

    assert team.status_code == 201
    assert team.json()["runtime_space_id"] == str(runtime_space_id)
    assert task.status_code == 201
    assert task.json()["runtime_space_id"] == str(runtime_space_id)
    assert stored_team is not None
    assert stored_team.runtime_space_id == runtime_space_id
    assert len(stored_runs) == 1
    assert stored_runs[0].runtime_space_id == runtime_space_id


def test_runtime_space_rejects_cross_workspace_team_binding() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    other_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Other Team",
        team_type="research",
    )
    session.add(other_team)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={
            "name": "Invalid team space",
            "scope": "team",
            "target_id": str(other_team.id),
        },
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/runtime-spaces",
        headers=_headers(other_owner.id),
        json={
            "name": "Valid other team space",
            "scope": "team",
            "target_id": str(other_team.id),
        },
    )

    assert response.status_code == 400
    assert "Team not found" in response.json()["error"]["message"]
    assert forbidden.status_code == 201


def test_viewer_cannot_manage_runtime_spaces() -> None:
    client, session = _client()
    viewer, workspace = _seed_workspace(session, role="viewer")

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(viewer.id),
        json={"name": "Viewer space"},
    )

    assert response.status_code == 403
    assert session.query(RuntimeSpace).count() == 0
    assert session.query(RuntimeSpaceEvent).count() == 0


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
    seed_session = session_factory()
    settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        database_url="sqlite+pysqlite:///:memory:",
    )
    app = create_app(settings)
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis_client, RedisKeyBuilder(settings.redis_key_prefix), "agent.run")

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), seed_session


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
