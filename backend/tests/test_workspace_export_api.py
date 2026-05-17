import json
from collections.abc import Generator

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_workspace_metadata_export_is_scoped_and_audited() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner-space")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other-space")
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    other_agent = AgentProfile(workspace_id=other_workspace.id, name="Other", role="researcher")
    team = AgentTeam(workspace_id=workspace.id, name="Research Team", team_type="research")
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Q2 Research")
    session.add_all([agent, other_agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "filename=\"owner-space-workspace-export.json\"" in response.headers[
        "content-disposition"
    ]
    payload = json.loads(response.content)
    assert payload["manifest"]["format_version"] == "workspace-export.v1"
    assert payload["workspace"]["id"] == str(workspace.id)
    assert payload["manifest"]["counts"]["agents"] == 1
    assert payload["manifest"]["counts"]["teams"] == 1
    assert payload["manifest"]["counts"]["team_members"] == 1
    assert payload["manifest"]["counts"]["tasks"] == 1
    assert payload["agents"][0]["id"] == str(agent.id)
    assert all(item["workspace_id"] == str(workspace.id) for item in payload["agents"])
    assert other_agent.id not in {item["id"] for item in payload["agents"]}

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.export.created",
        )
    )
    assert audit is not None
    assert audit.user_id == owner.id


def test_workspace_metadata_export_denies_cross_workspace_access() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    response = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={},
    )

    assert response.status_code == 403
    assert workspace.id != other_workspace.id


def test_workspace_metadata_import_supports_dry_run_and_committed_import() -> None:
    client, session = _client()
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    agent = AgentProfile(workspace_id=source_workspace.id, name="Researcher", role="researcher")
    team = AgentTeam(workspace_id=source_workspace.id, name="Research Team", team_type="research")
    task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Q2 Research",
        domain_type="research",
    )
    session.add_all([agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=source_workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
        )
    )
    session.commit()
    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False, "include_runs": False, "include_files": False},
    )
    export_payload = json.loads(export_response.content)

    dry_run = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    after_dry_run_agents = session.scalars(
        select(AgentProfile).where(AgentProfile.workspace_id == target_workspace.id)
    ).all()
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert dry_run.status_code == 200
    assert dry_run.json()["created_counts"]["agents"] == 1
    assert after_dry_run_agents == []
    assert committed.status_code == 200
    body = committed.json()
    assert body["dry_run"] is False
    assert body["created_counts"]["agents"] == 1
    assert body["created_counts"]["teams"] == 1
    assert body["created_counts"]["team_members"] == 1
    assert body["created_counts"]["tasks"] == 1
    assert len(body["id_map"]["agents"]) == 1

    imported_agent = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher",
        )
    )
    imported_team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == target_workspace.id,
            AgentTeam.name == "Imported Research Team",
        )
    )
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Q2 Research",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.import.created",
        )
    )
    assert imported_agent is not None
    assert imported_team is not None
    assert imported_task is not None
    assert imported_task.status == "draft"
    assert audit is not None
    assert audit.user_id == target_user.id


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
