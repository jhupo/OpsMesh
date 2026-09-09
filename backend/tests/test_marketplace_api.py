from collections.abc import Generator
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.marketplace import MarketplaceInstallRequest
from backend.app.capabilities.models import (
    McpServer,
    McpToolAllowlist,
    Skill,
    WorkspaceSkillInstall,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.marketplace.models import (
    MarketplaceListing,
    TalentListing,
    WorkspaceAgentInstall,
    WorkspaceMarketplaceInstall,
)
from backend.app.marketplace.resource_service import MarketplaceService
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.redis.dependencies import get_redis_client
from backend.app.reviews.llm import LlmReviewResult
from backend.app.tasks.models import Task, TaskMessage
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


@pytest.fixture(autouse=True)
def approve_resource_reviews_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_review(self, **kwargs):  # noqa: ANN001, ANN202
        return LlmReviewResult(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        )

    monkeypatch.setattr("backend.app.reviews.llm.LlmResourceReviewer.review", fake_review)


def test_workspace_can_publish_public_plugin_listing_and_install_it() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")

    created = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/marketplace-listings",
        headers=_headers(publisher.id),
        json={
            "listing_type": "plugin",
            "name": "Linear Sync",
            "summary": "Syncs issue state into tasks.",
            "version": "1.2.0",
            "visibility": "public",
            "tags": ["productivity", "linear"],
            "manifest": {
                "entrypoint": "plugin.py",
                "permissions": ["tasks.write"],
                "api_key": "plugin-secret",
            },
            "metadata": {"homepage": "https://plugins.example.test/linear"},
        },
    )

    assert created.status_code == 201
    listing_id = created.json()["id"]
    assert created.json()["listing_type"] == "plugin"
    assert created.json()["status"] == "pending_approval"
    assert created.json()["manifest"]["api_key"] == "[redacted]"

    approvals = client.get(
        f"/api/v1/workspaces/{publisher_workspace.id}/approvals",
        headers=_headers(publisher.id),
    )
    approval_id = approvals.json()["items"][0]["id"]
    approved = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/approvals/{approval_id}/approve",
        headers=_headers(publisher.id),
        json={"reason": "publish public plugin"},
    )

    market = client.get("/api/v1/marketplace?listing_type=plugin&query=Linear")
    assert approvals.status_code == 200
    assert approvals.json()["total"] == 1
    assert approved.status_code == 200
    assert market.status_code == 200
    assert market.json()["total"] == 1
    assert market.json()["items"][0]["id"] == listing_id
    assert "plugin-secret" not in str(market.json())

    installed = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/marketplace-listings/{listing_id}/install",
        headers=_headers(buyer.id),
        json={"config": {"enabled": True, "token": "buyer-secret"}},
    )

    assert installed.status_code == 201
    installed_body = installed.json()
    assert installed_body["workspace_id"] == str(buyer_workspace.id)
    assert installed_body["listing_type"] == "plugin"
    assert installed_body["installed_name"] == "Linear Sync"
    assert installed_body["installed_manifest"]["api_key"] == "[redacted]"
    assert installed_body["config"]["token"] == "[redacted]"
    assert "buyer-secret" not in str(installed_body)

    installs = client.get(
        f"/api/v1/workspaces/{buyer_workspace.id}/marketplace-installs?listing_type=plugin",
        headers=_headers(buyer.id),
    )
    assert installs.status_code == 200
    assert installs.json()["total"] == 1
    assert installs.json()["items"][0]["id"] == installed_body["id"]
    assert session.query(MarketplaceListing).count() == 1
    assert session.query(WorkspaceMarketplaceInstall).count() == 1


def test_private_plugin_listing_is_workspace_scoped() -> None:
    client, session = _client()
    owner, owner_workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    created = client.post(
        f"/api/v1/workspaces/{owner_workspace.id}/marketplace-listings",
        headers=_headers(owner.id),
        json={
            "listing_type": "plugin",
            "name": "Internal Deploy Guard",
            "visibility": "private",
            "manifest": {"entrypoint": "guard.py"},
        },
    )

    assert created.status_code == 201
    listing_id = created.json()["id"]
    assert created.json()["visibility"] == "private"
    assert created.json()["status"] == "active"

    public_market = client.get("/api/v1/marketplace?listing_type=plugin")
    foreign_install = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/marketplace-listings/{listing_id}/install",
        headers=_headers(other.id),
        json={},
    )
    owner_install = client.post(
        f"/api/v1/workspaces/{owner_workspace.id}/marketplace-listings/{listing_id}/install",
        headers=_headers(owner.id),
        json={},
    )

    assert public_market.status_code == 200
    assert public_market.json()["total"] == 0
    assert foreign_install.status_code == 404
    assert owner_install.status_code == 201
    assert session.query(WorkspaceMarketplaceInstall).count() == 1


def test_private_plugin_listing_skips_resource_review_by_default(monkeypatch) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="plugin-owner@example.com", slug="plugin")
    called = False

    def require_review(self, **kwargs):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return LlmReviewResult(
            required=True,
            risk_level="high",
            reasons=["llm_review.requires_admin"],
            signals={"reviewer": "llm", "verdict": "review"},
        )

    monkeypatch.setattr("backend.app.reviews.llm.LlmResourceReviewer.review", require_review)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/marketplace-listings",
        headers=_headers(owner.id),
        json={
            "listing_type": "plugin",
            "name": "Private Shell Plugin",
            "visibility": "private",
            "manifest": {"permissions": ["shell", "production"]},
        },
    )

    assert created.status_code == 201
    assert created.json()["status"] == "active"
    assert called is False


def test_private_plugin_review_can_be_enabled_per_workspace(monkeypatch) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(
        session,
        email="plugin-reviewer@example.com",
        slug="plugin-reviewer",
    )
    workspace.settings = {"resource_review": {"private_resources": {"plugin": True}}}
    session.commit()

    def require_review(self, **kwargs):  # noqa: ANN001, ANN202
        return LlmReviewResult(
            required=True,
            risk_level="high",
            reasons=["llm_review.requires_admin"],
            signals={"reviewer": "llm", "verdict": "review"},
        )

    monkeypatch.setattr("backend.app.reviews.llm.LlmResourceReviewer.review", require_review)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/marketplace-listings",
        headers=_headers(owner.id),
        json={
            "listing_type": "plugin",
            "name": "Reviewed Private Plugin",
            "visibility": "private",
            "manifest": {"permissions": ["shell", "production"]},
        },
    )

    assert created.status_code == 201
    assert created.json()["status"] == "pending_approval"


def test_marketplace_install_agent_listing_creates_agent_profile() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="agent-publisher@example.com",
        slug="agent-publisher",
    )
    buyer, buyer_workspace = _seed_workspace(
        session,
        email="agent-buyer@example.com",
        slug="agent-buyer",
    )
    created = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/marketplace-listings",
        headers=_headers(publisher.id),
        json={
            "listing_type": "agent",
            "name": "Research Operator",
            "visibility": "public",
            "source_resource_id": str(
                _seed_agent(session, publisher_workspace, name="Research Operator").id
            ),
            "manifest": {
                "agent": {
                    "role": "researcher",
                    "instructions": "Research public sources.",
                    "skills": {"skills": ["research"]},
                }
            },
        },
    )
    _approve_resource_review(client, publisher_workspace.id, publisher.id)

    installed = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/marketplace-listings/{created.json()['id']}/install",
        headers=_headers(buyer.id),
        json={"config": {"agent_name": "Research Operator Copy"}},
    )

    assert installed.status_code == 201
    installed_agent = session.get(AgentProfile, UUID(installed.json()["installed_resource_id"]))
    assert installed_agent is not None
    assert installed_agent.workspace_id == buyer_workspace.id
    assert installed_agent.name == "Research Operator Copy"
    assert installed_agent.role == "researcher"
    assert installed_agent.status == "active"


def test_marketplace_install_rolls_back_provisioned_agent_on_install_conflict() -> None:
    _, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="agent-rollback-publisher@example.com",
        slug="agent-rollback-publisher",
    )
    buyer, buyer_workspace = _seed_workspace(
        session,
        email="agent-rollback-buyer@example.com",
        slug="agent-rollback-buyer",
    )
    source_agent = _seed_agent(session, publisher_workspace, name="Rollback Research")
    listing = MarketplaceListing(
        workspace_id=publisher_workspace.id,
        owner_user_id=publisher.id,
        source_resource_id=source_agent.id,
        listing_type="agent",
        visibility="public",
        status="public",
        name="Rollback Research",
        manifest={"agent": {"role": "researcher"}},
    )
    session.add(listing)
    session.flush()
    existing_install = WorkspaceMarketplaceInstall(
        workspace_id=buyer_workspace.id,
        marketplace_listing_id=listing.id,
        listing_type="agent",
        installed_name="Rollback Research",
        installed_version="1.0.0",
        installed_manifest=dict(listing.manifest),
        config={},
    )
    session.add(existing_install)
    session.commit()
    existing_install.status = "disabled"
    session.commit()
    agent_count_before = (
        session.query(AgentProfile).filter_by(workspace_id=buyer_workspace.id).count()
    )

    with pytest.raises(DatabaseConflictError):
        MarketplaceService(session).install_listing(
            workspace_id=buyer_workspace.id,
            user_id=buyer.id,
            listing_id=listing.id,
            data=MarketplaceInstallRequest(config={"agent_name": "Should Roll Back"}),
        )

    assert session.query(AgentProfile).filter_by(workspace_id=buyer_workspace.id).count() == (
        agent_count_before
    )
    assert (
        session.query(WorkspaceMarketplaceInstall)
        .filter_by(
            workspace_id=buyer_workspace.id,
            marketplace_listing_id=listing.id,
        )
        .count()
        == 1
    )


def test_marketplace_install_skill_listing_creates_workspace_skill_install() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="skill-publisher@example.com",
        slug="skill-publisher",
    )
    buyer, buyer_workspace = _seed_workspace(
        session,
        email="skill-buyer@example.com",
        slug="skill-buyer",
    )
    source_skill = Skill(
        key="public.research",
        name="Research Skill",
        version="1.0.0",
        description="Research workflow",
        capability_keys=["web.search"],
        manifest={"required_tools": ["search"]},
        owner_workspace_id=publisher_workspace.id,
        visibility="private",
    )
    session.add(source_skill)
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/marketplace-listings",
        headers=_headers(publisher.id),
        json={
            "listing_type": "skill",
            "name": "Research Skill",
            "visibility": "public",
            "source_resource_id": str(source_skill.id),
            "manifest": {
                "key": "public.research",
                "capability_keys": ["web.search"],
                "manifest": {"required_tools": ["search"]},
            },
        },
    )
    _approve_resource_review(client, publisher_workspace.id, publisher.id)

    installed = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/marketplace-listings/{created.json()['id']}/install",
        headers=_headers(buyer.id),
        json={"config": {"level": "strict"}},
    )

    assert installed.status_code == 201
    skill_install = session.get(
        WorkspaceSkillInstall,
        UUID(installed.json()["installed_resource_id"]),
    )
    assert skill_install is not None
    assert skill_install.workspace_id == buyer_workspace.id
    assert skill_install.installed_key == "public.research"
    assert skill_install.config == {"level": "strict"}


def test_marketplace_install_mcp_server_listing_creates_server_and_tools() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="mcp-publisher@example.com",
        slug="mcp-publisher",
    )
    buyer, buyer_workspace = _seed_workspace(
        session,
        email="mcp-buyer@example.com",
        slug="mcp-buyer",
    )
    source_server = McpServer(
        workspace_id=publisher_workspace.id,
        name="Public Search MCP",
        server_type="streamable_http",
        connection={"url": "https://mcp.example.test/rpc"},
        visibility="private",
    )
    session.add(source_server)
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{publisher_workspace.id}/marketplace-listings",
        headers=_headers(publisher.id),
        json={
            "listing_type": "mcp_server",
            "name": "Public Search MCP",
            "visibility": "public",
            "source_resource_id": str(source_server.id),
            "manifest": {
                "server_type": "streamable_http",
                "connection": {"url": "https://mcp.example.test/rpc"},
                "tools": [
                    {
                        "tool_name": "search",
                        "capability_key": "web.search",
                        "risk_level": "low",
                    }
                ],
            },
        },
    )
    _approve_resource_review(client, publisher_workspace.id, publisher.id)

    installed = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/marketplace-listings/{created.json()['id']}/install",
        headers=_headers(buyer.id),
        json={},
    )

    assert installed.status_code == 201
    server = session.get(McpServer, UUID(installed.json()["installed_resource_id"]))
    assert server is not None
    assert server.workspace_id == buyer_workspace.id
    assert server.name == "Public Search MCP"
    assert server.visibility == "private"
    allow = session.query(McpToolAllowlist).filter_by(mcp_server_id=server.id).one()
    assert allow.tool_name == "search"
    assert allow.capability_key == "web.search"


def test_public_plugin_listing_requires_resource_review_before_market_visibility(
    monkeypatch,
) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="reviewer@example.com", slug="reviewer")

    def require_review(self, **kwargs):  # noqa: ANN001, ANN202
        return LlmReviewResult(
            required=True,
            risk_level="high",
            reasons=["llm_review.requires_admin"],
            signals={"reviewer": "codex-auto-review", "verdict": "review"},
        )

    monkeypatch.setattr("backend.app.reviews.llm.LlmResourceReviewer.review", require_review)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/marketplace-listings",
        headers=_headers(owner.id),
        json={
            "listing_type": "plugin",
            "name": "Production Shell Plugin",
            "visibility": "public",
            "manifest": {"permissions": ["shell", "production"]},
        },
    )
    market_before = client.get("/api/v1/marketplace?listing_type=plugin")
    approvals = client.get(
        f"/api/v1/workspaces/{workspace.id}/approvals",
        headers=_headers(owner.id),
    )
    approval_id = approvals.json()["items"][0]["id"]
    approved = client.post(
        f"/api/v1/workspaces/{workspace.id}/approvals/{approval_id}/approve",
        headers=_headers(owner.id),
        json={"reason": "reviewed"},
    )
    market_after = client.get("/api/v1/marketplace?listing_type=plugin")

    assert created.status_code == 201
    assert created.json()["status"] == "pending_approval"
    assert market_before.status_code == 200
    assert market_before.json()["total"] == 0
    assert approvals.status_code == 200
    assert approvals.json()["total"] == 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert market_after.status_code == 200
    assert market_after.json()["total"] == 1
    assert market_after.json()["items"][0]["id"] == created.json()["id"]


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
            "metadata": {
                "token": "listing-token",
                "nested": {"base_url": "https://listing.example.test/private"},
                "safe": "visible",
            },
        },
    )
    assert published.status_code == 201
    if published.json()["status"] == "pending_approval":
        _approve_resource_review(client, publisher_workspace.id, publisher.id)
    listing_id = published.json()["id"]
    assert published.json()["listing_metadata"]["token"] == "[redacted]"
    assert published.json()["listing_metadata"]["nested"]["base_url"] == "[redacted]"

    market = client.get("/api/v1/talent-market?skill=research")
    assert market.status_code == 200
    assert market.json()["total"] == 1
    assert market.json()["items"][0]["id"] == listing_id
    assert market.json()["items"][0]["listing_metadata"]["token"] == "[redacted]"
    assert "listing-token" not in str(market.json())
    assert "listing.example.test/private" not in str(market.json())

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
    if published.json()["status"] == "pending_approval":
        _approve_resource_review(client, publisher_workspace.id, publisher.id)

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


def test_talent_install_uses_frozen_public_snapshot_without_private_workspace_refs() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    credential = ModelProviderCredential(
        workspace_id=publisher_workspace.id,
        created_by_user_id=publisher.id,
        name="Publisher OpenAI",
        provider="openai",
        default_model="gpt-4.1",
        encrypted_api_key="encrypted",
        api_key_fingerprint="sha256:test",
        encryption_key_id="test-key",
    )
    buyer_default_credential = ModelProviderCredential(
        workspace_id=buyer_workspace.id,
        created_by_user_id=buyer.id,
        name="Buyer OpenAI",
        provider="openai",
        default_model="gpt-4.1-mini",
        encrypted_api_key="encrypted-buyer",
        api_key_fingerprint="sha256:buyer",
        encryption_key_id="test-key",
        is_default=True,
        health_status="healthy",
    )
    session.add_all([credential, buyer_default_credential])
    session.flush()
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Image Pro",
        role="designer",
        description="Published version",
        instructions="Use the public image workflow.",
        model="gpt-4.1",
        model_provider_credential_id=credential.id,
        model_settings={"temperature": 0.2, "workspace_id": str(publisher_workspace.id)},
        capabilities={"tools": ["image.generate"], "runtime_space_id": "source-runtime"},
        skills={
            "skills": ["image"],
            "installed_skill_ids": ["source-private-install"],
            "nested": {"skill_install_id": "source-private-install"},
        },
        tool_policy={
            "mcp_tools": ["generate_image"],
            "mcp_server_ids": ["source-server"],
            "nested": {"credential_reference_id": "source-secret"},
        },
        runtime_policy={
            "provider": "docker",
            "runtime_space_id": "source-runtime",
            "limits": {"cpu": 2},
        },
        memory_policy={"episodic_memory": {"capture_enabled": False}},
        approval_policy={"mode": "default", "credential_id": "source-secret"},
        version=1,
    )
    session.add(source_agent)
    session.commit()

    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Image Pro",
        skill_tags=["image"],
        capability_tags=["image.generate"],
    )
    source_agent.instructions = "Private draft after publish."
    source_agent.model = "private-provider/model"
    source_agent.skills = {"installed_skill_ids": ["new-private-install"]}
    source_agent.version = 2
    session.commit()

    hired = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/talent-market/{listing['id']}/hire",
        headers=_headers(buyer.id),
        json={},
    )

    assert hired.status_code == 201
    agent = hired.json()["agent"]
    assert agent["workspace_id"] == str(buyer_workspace.id)
    assert agent["instructions"] == "Use the public image workflow."
    assert agent["model"] == "gpt-4.1"
    assert agent["model_provider_credential_id"] is None
    assert agent["model_provider"]["source"] == "workspace_default"
    assert agent["model_provider"]["provider"] == "openai"
    assert agent["model_provider"]["credential_name"] == "Buyer OpenAI"
    assert agent["model_provider"]["credential_id"] == str(buyer_default_credential.id)
    assert agent["model_provider"]["selected_model"] == "gpt-4.1"
    assert agent["model_provider"]["default_model"] == "gpt-4.1-mini"
    assert agent["model_provider"]["api_key_fingerprint"] == "sha256:buyer"
    assert agent["model_provider"]["readiness_status"] == "ready"
    assert str(credential.id) not in str(agent["model_provider"])
    assert "Publisher OpenAI" not in str(agent["model_provider"])
    assert "encrypted" not in str(hired.json())
    assert agent["skills"] == {"skills": ["image"], "nested": {}}
    assert agent["tool_policy"] == {"mcp_tools": ["generate_image"], "nested": {}}
    assert agent["runtime_policy"] == {"provider": "docker", "limits": {"cpu": 2}}
    assert agent["memory_policy"] == {
        "context_budget": {
            "max_input_tokens": None,
            "context_window_tokens": None,
            "output_reserve_tokens": 4_096,
            "safety_margin_tokens": 1_024,
        },
        "working_memory": {
            "enabled": True,
            "ttl_seconds": 86_400,
            "max_entries": 64,
            "max_entry_tokens": 2_048,
        },
        "episodic_memory": {
            "capture_enabled": False,
            "retrieval_enabled": True,
            "retention_days": 180,
            "max_results": 8,
            "default_importance": 30,
        },
    }
    assert agent["approval_policy"] == {"mode": "default"}
    assert agent["model_settings"] == {"temperature": 0.2}
    assert agent["capabilities"] == {"tools": ["image.generate"]}


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


def test_hr_recommends_talent_for_unassigned_task_work_packages() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Frontend Pro",
        role="frontend_engineer",
    )
    team = AgentTeam(
        workspace_id=buyer_workspace.id,
        name="Product Team",
        team_type="software",
    )
    task = Task(
        workspace_id=buyer_workspace.id,
        created_by_user_id=buyer.id,
        agent_team_id=team.id,
        title="Build dashboard",
        team_snapshot={"team": {"id": str(team.id), "team_type": "software"}},
        project_plan={
            "work_packages": [
                {
                    "package_id": "frontend-ui",
                    "title": "Frontend UI",
                    "required_role": "frontend_engineer",
                    "required_skills": ["react", "ui"],
                    "assigned_agent_profile_id": None,
                    "expected_artifacts": ["pull_request"],
                }
            ]
        },
    )
    session.add_all([source_agent, team, task])
    session.commit()
    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Frontend Engineer",
        skill_tags=["react", "ui"],
        capability_tags=["code.execute"],
    )

    response = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/tasks/{task.id}/talent-market/recommendations",
        headers=_headers(buyer.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["missing_work_packages"] == [
        {
            "package_id": "frontend-ui",
            "title": "Frontend UI",
            "required_role": "frontend_engineer",
            "required_skills": ["react", "ui"],
            "expected_artifacts": ["pull_request"],
        }
    ]
    assert body["uncovered_roles"] == ["frontend_engineer"]
    recommendation = body["recommended_roles"][0]
    assert recommendation["role"] == "frontend_engineer"
    assert recommendation["candidates"][0]["listing"]["id"] == listing["id"]

    messages = (
        session.query(TaskMessage)
        .filter(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.sequence)
        .all()
    )
    assert [message.message_type for message in messages] == ["hr.staffing_recommendation"]
    assert messages[0].payload["missing_work_packages"] == body["missing_work_packages"]
    assert messages[0].payload["uncovered_roles"] == ["frontend_engineer"]


def test_owner_can_hire_recommended_talent_for_task_gap_without_rewriting_snapshot() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Frontend Pro",
        role="frontend_engineer",
    )
    team = AgentTeam(
        workspace_id=buyer_workspace.id,
        name="Product Team",
        team_type="software",
    )
    session.add_all([source_agent, team])
    session.flush()
    frozen_snapshot = {
        "team": {"id": str(team.id), "team_type": "software"},
        "members": [],
    }
    project_plan = {
        "work_packages": [
            {
                "package_id": "frontend-ui",
                "title": "Frontend UI",
                "required_role": "frontend_engineer",
                "required_skills": ["react", "ui"],
                "assigned_agent_profile_id": None,
                "expected_artifacts": ["pull_request"],
            }
        ]
    }
    task = Task(
        workspace_id=buyer_workspace.id,
        created_by_user_id=buyer.id,
        agent_team_id=team.id,
        title="Build dashboard",
        team_snapshot=frozen_snapshot,
        project_plan=project_plan,
    )
    session.add(task)
    session.commit()
    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Frontend Engineer",
        skill_tags=["react", "ui"],
        capability_tags=["code.execute"],
    )

    response = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/tasks/{task.id}/talent-market/hire",
        headers=_headers(buyer.id),
        json={
            "listing_id": listing["id"],
            "work_package_id": "frontend-ui",
            "agent_name": "Frontend Pro Copy",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workspace_id"] == str(buyer_workspace.id)
    assert body["agent"]["name"] == "Frontend Pro Copy"

    team_member = session.query(AgentTeamMember).one()
    assert team_member.agent_team_id == team.id
    assert team_member.team_role == "frontend_engineer"
    assert str(team_member.agent_profile_id) == body["installed_agent_profile_id"]

    session.refresh(task)
    assert task.team_snapshot == frozen_snapshot
    assert task.project_plan == project_plan

    messages = (
        session.query(TaskMessage)
        .filter(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.sequence)
        .all()
    )
    assert [message.message_type for message in messages] == ["hr.hire_confirmed"]
    assert messages[0].payload["work_package_id"] == "frontend-ui"
    assert messages[0].payload["installed_agent_profile_id"] == body["installed_agent_profile_id"]


def test_task_gap_hire_rejects_already_assigned_work_package() -> None:
    client, session = _client()
    publisher, publisher_workspace = _seed_workspace(
        session,
        email="publisher@example.com",
        slug="publisher",
    )
    buyer, buyer_workspace = _seed_workspace(session, email="buyer@example.com", slug="buyer")
    source_agent = AgentProfile(
        workspace_id=publisher_workspace.id,
        name="Frontend Pro",
        role="frontend_engineer",
    )
    assigned_agent = AgentProfile(
        workspace_id=buyer_workspace.id,
        name="Existing Frontend",
        role="frontend_engineer",
    )
    team = AgentTeam(
        workspace_id=buyer_workspace.id,
        name="Product Team",
        team_type="software",
    )
    session.add_all([source_agent, assigned_agent, team])
    session.flush()
    task = Task(
        workspace_id=buyer_workspace.id,
        created_by_user_id=buyer.id,
        agent_team_id=team.id,
        title="Build dashboard",
        team_snapshot={"team": {"id": str(team.id), "team_type": "software"}},
        project_plan={
            "work_packages": [
                {
                    "package_id": "frontend-ui",
                    "title": "Frontend UI",
                    "required_role": "frontend_engineer",
                    "required_skills": ["react", "ui"],
                    "assigned_agent_profile_id": str(assigned_agent.id),
                    "expected_artifacts": ["pull_request"],
                }
            ]
        },
    )
    session.add(task)
    session.commit()
    listing = _publish_listing(
        client,
        publisher,
        publisher_workspace,
        source_agent,
        title="Frontend Engineer",
        skill_tags=["react", "ui"],
        capability_tags=["code.execute"],
    )

    response = client.post(
        f"/api/v1/workspaces/{buyer_workspace.id}/tasks/{task.id}/talent-market/hire",
        headers=_headers(buyer.id),
        json={"listing_id": listing["id"], "work_package_id": "frontend-ui"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == ("Task work package does not have a staffing gap")
    assert session.query(AgentTeamMember).count() == 0


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
    app.dependency_overrides[get_redis_client] = lambda: fakeredis.FakeRedis(decode_responses=True)
    return TestClient(app), session


def _seed_workspace(session: Session, *, email: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _seed_agent(
    session: Session,
    workspace: Workspace,
    *,
    name: str,
    role: str = "researcher",
) -> AgentProfile:
    agent = AgentProfile(workspace_id=workspace.id, name=name, role=role)
    session.add(agent)
    session.commit()
    return agent


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
    listing = response.json()
    if listing["status"] == "pending_approval":
        _approve_resource_review(client, workspace.id, publisher.id)
        listing = {**listing, "status": "public"}
    return listing


def _approve_resource_review(
    client: TestClient,
    workspace_id: object,
    user_id: object,
) -> None:
    approvals = client.get(
        f"/api/v1/workspaces/{workspace_id}/approvals",
        headers=_headers(user_id),
    )
    assert approvals.status_code == 200
    assert approvals.json()["total"] >= 1
    approval_id = approvals.json()["items"][0]["id"]
    approved = client.post(
        f"/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
        headers=_headers(user_id),
        json={"reason": "test approval"},
    )
    assert approved.status_code == 200


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
