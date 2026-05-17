from collections.abc import Generator

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.marketplace.models import TalentListing, WorkspaceAgentInstall
from backend.app.redis.dependencies import get_redis_client
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_owner_can_publish_and_another_workspace_can_hire_agent_into_team() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Research Pro",
        role="researcher",
        description="Finds market evidence",
        instructions="Research deeply.",
        skills={"skills": ["web-research"]},
        tool_policy={"mcp_tools": ["search"]},
        runtime_policy={"provider": "docker"},
    )
    team = AgentTeam(
        workspace_id=buyer_workspace.id,
        name="Market Team",
        team_type="marketing",
    )
    session.add_all([source_agent, team])
    session.commit()

    published = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/talent-listings",
        headers=_headers(publisher.id),
        json={
            "agent_profile_id": str(source_agent.id),
            "title": "Market Research Specialist",
            "summary": "Researches competitors and markets",
            "skill_tags": ["research", "market"],
            "capability_tags": ["web.search"],
            "required_tools": ["search"],
            "default_team_role": "research_specialist",
        },
    )
    assert published.status_code == 201
    listing_id = published.json()["id"]

    market = client.get("/api/v1/talent-market?skill=research")
    assert market.status_code == 200
    assert market.json()["total"] == 1
    assert market.json()["items"][0]["id"] == listing_id

    hired = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing_id}/hire",
        headers=_headers(buyer.id),
        json={"team_id": str(team.id), "agent_name": "Research Pro Copy"},
    )
    assert hired.status_code == 201
    hired_body = hired.json()
    assert hired_body["workspace_id"] == str(buyer_workspace.id)
    assert hired_body["agent"]["workspace_id"] == str(buyer_workspace.id)
    assert hired_body["agent"]["name"] == "Research Pro Copy"
    assert hired_body["agent"]["tool_policy"] == {"mcp_tools": ["search"]}

    assert session.query(TalentListing).count() == 1
    assert session.query(WorkspaceAgentInstall).count() == 1
    installed_agent = (
        session.query(AgentProfile)
        .filter(AgentProfile.workspace_id == buyer_workspace.id, AgentProfile.role == "researcher")
        .one()
    )
    assert installed_agent.id != source_agent.id
    team_member = session.query(AgentTeamMember).one()
    assert team_member.agent_profile_id == installed_agent.id
    assert team_member.team_role == "research_specialist"


def test_hiring_same_listing_twice_returns_conflict() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Designer",
        role="designer",
    )
    session.add(source_agent)
    session.commit()
    published = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/talent-listings",
        headers=_headers(publisher.id),
        json={"agent_profile_id": str(source_agent.id), "title": "Designer"},
    )
    listing_id = published.json()["id"]

    first = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing_id}/hire",
        headers=_headers(buyer.id),
        json={},
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing_id}/hire",
        headers=_headers(buyer.id),
        json={},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["message"] == "Talent listing is already hired in workspace"


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
    app = create_app(Settings(environment="test", log_format="text", internal_api_token=TOKEN))

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: fakeredis.FakeRedis(
        decode_responses=True
    )
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
