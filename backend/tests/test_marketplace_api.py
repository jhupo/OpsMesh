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


def test_talent_install_can_be_pinned_checked_and_upgraded_to_new_listing_version() -> None:
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
        description="v1 research",
        instructions="Research v1.",
        version=1,
        tool_policy={"mcp_tools": ["search"]},
    )
    session.add(source_agent)
    session.commit()
    v1_listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Research Pro",
        skill_tags=["research"],
        capability_tags=["web.search"],
    )
    hired = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{v1_listing['id']}/hire",
        headers=_headers(buyer.id),
        json={"agent_name": "Research Pro Copy"},
    )
    assert hired.status_code == 201
    install_id = hired.json()["id"]
    assert hired.json()["installed_version"] == 1
    assert hired.json()["pinned_version"] is True

    source_agent.description = "v2 research"
    source_agent.instructions = "Research v2."
    source_agent.version = 2
    session.commit()
    v2_listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Research Pro v2",
        skill_tags=["research", "market"],
        capability_tags=["web.search"],
    )

    status_response = client.get(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-installs/{install_id}/upgrade-status",
        headers=_headers(buyer.id),
    )
    unpinned = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-installs/{install_id}/pin",
        headers=_headers(buyer.id),
        json={"pinned_version": False},
    )
    upgraded = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-installs/{install_id}/upgrade",
        headers=_headers(buyer.id),
        json={"target_listing_id": v2_listing["id"], "keep_pinned": True},
    )

    assert status_response.status_code == 200
    assert status_response.json()["has_update"] is True
    assert status_response.json()["latest_listing"]["id"] == v2_listing["id"]
    assert unpinned.status_code == 200
    assert unpinned.json()["pinned_version"] is False
    assert upgraded.status_code == 200
    upgraded_body = upgraded.json()
    assert upgraded_body["installed_version"] == 2
    assert upgraded_body["current_talent_listing_id"] == v2_listing["id"]
    assert upgraded_body["pinned_version"] is True
    assert upgraded_body["agent"]["description"] == "v2 research"
    assert upgraded_body["agent"]["instructions"] == "Research v2."

    list_response = client.get(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-installs",
        headers=_headers(buyer.id),
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["installed_version"] == 2


def test_talent_listing_metrics_and_reviews_are_public_and_workspace_scoped() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    non_buyer, non_buyer_workspace = _seed_workspace(
        session,
        email="nonbuyer@example.com",
        slug="nonbuyer",
    )
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Research Pro",
        role="researcher",
        description="v1 research",
    )
    session.add(source_agent)
    session.commit()
    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Research Pro",
        skill_tags=["research"],
        capability_tags=[],
    )

    blocked_review = client.post(
        f"/api/v1/workspaces/{non_buyer_workspace.id}/talent-market/{listing['id']}/reviews",
        headers=_headers(non_buyer.id),
        json={"rating": 5, "title": "Cannot review before hire"},
    )
    hired = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing['id']}/hire",
        headers=_headers(buyer.id),
        json={},
    )
    first_review = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing['id']}/reviews",
        headers=_headers(buyer.id),
        json={
            "workspace_agent_install_id": hired.json()["id"],
            "rating": 4,
            "title": "Useful researcher",
            "body": "Good at evidence gathering.",
        },
    )
    updated_review = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing['id']}/reviews",
        headers=_headers(buyer.id),
        json={
            "workspace_agent_install_id": hired.json()["id"],
            "rating": 5,
            "title": "Great researcher",
            "body": "Improved after more usage.",
        },
    )
    metrics = client.get(f"/api/v1/talent-market/{listing['id']}/metrics")
    reviews = client.get(f"/api/v1/talent-market/{listing['id']}/reviews")

    assert blocked_review.status_code == 404
    assert hired.status_code == 201
    assert first_review.status_code == 201
    assert first_review.json()["rating"] == 4
    assert updated_review.status_code == 201
    assert updated_review.json()["rating"] == 5
    assert updated_review.json()["id"] == first_review.json()["id"]
    assert metrics.status_code == 200
    assert metrics.json()["install_count"] == 1
    assert metrics.json()["review_count"] == 1
    assert metrics.json()["average_rating"] == 5.0
    assert reviews.status_code == 200
    assert reviews.json()["total"] == 1
    assert reviews.json()["items"][0]["title"] == "Great researcher"


def test_talent_install_upgrade_rejects_foreign_workspace_install() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Designer",
        role="designer",
    )
    session.add(source_agent)
    session.commit()
    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Designer",
        skill_tags=["design"],
        capability_tags=[],
    )
    hired = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing['id']}/hire",
        headers=_headers(buyer.id),
        json={},
    )

    response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/talent-installs/{hired.json()['id']}/upgrade-status",
        headers=_headers(other.id),
    )

    assert response.status_code == 404


def test_hr_recommendations_rank_market_candidates_and_detect_team_gaps() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    team = AgentTeam(
        workspace_id=buyer_workspace.id,
        name="Research Team",
        team_type="research",
    )
    manager = AgentProfile(
        workspace_id=buyer_workspace.id,
        name="Existing PM",
        role="project_manager",
    )
    session.add_all([team, manager])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=buyer_workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="manager",
        )
    )
    research_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Market Researcher",
        role="researcher",
        description="Researches market and competitors",
    )
    weak_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="General Writer",
        role="writer",
        description="Writes summaries",
    )
    session.add_all([research_agent, weak_agent])
    session.commit()

    research_listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        research_agent,
        title="Market Research Specialist",
        skill_tags=["research", "market"],
        capability_tags=["web.search"],
    )
    _publish_listing(
        client,
        publisher,
        publisher_workspace,
        weak_agent,
        title="General Writer",
        skill_tags=["writing"],
        capability_tags=[],
    )

    response = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/recommendations",
        headers=_headers(buyer.id),
        json={
            "objective": "完成 Q2 市场分析，包含竞品、趋势、数据结论",
            "team_type": "research",
            "team_id": str(team.id),
            "skill_tags": ["research", "market"],
            "capability_tags": ["web.search"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["team_type"] == "research"
    assert body["existing_team_roles"] == ["manager"]
    assert "researcher" in body["uncovered_roles"]
    research_role = next(item for item in body["recommended_roles"] if item["role"] == "researcher")
    assert research_role["candidates"][0]["listing"]["id"] == research_listing["id"]
    assert research_role["candidates"][0]["score"] > 0
    assert "岗位匹配" in research_role["candidates"][0]["matched_reasons"]


def test_hr_recommendations_reject_foreign_team() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    other_team = AgentTeam(workspace_id=other_workspace.id, name="Other Team", team_type="research")
    session.add(other_team)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/talent-market/recommendations",
        headers=_headers(owner.id),
        json={
            "objective": "找人做市场分析",
            "team_type": "research",
            "team_id": str(other_team.id),
        },
    )

    assert response.status_code == 404


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


def _publish_listing(
    client: TestClient,
    publisher: User,
    workspace: Workspace,
    agent: AgentProfile,
    *,
    title: str,
    skill_tags: list[str],
    capability_tags: list[str],
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/talent-listings",
        headers=_headers(publisher.id),
        json={
            "agent_profile_id": str(agent.id),
            "title": title,
            "skill_tags": skill_tags,
            "capability_tags": capability_tags,
        },
    )
    assert response.status_code == 201
    return response.json()


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
