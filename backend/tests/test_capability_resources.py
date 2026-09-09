from collections.abc import Generator
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import WorkspaceFile
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "capability-resource-test-token"


def test_resource_lifecycle_and_workspace_catalog() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="requirements.md",
        content_type="text/markdown",
        size_bytes=12,
        checksum_sha256="a" * 64,
        storage_key=f"workspaces/{workspace.id}/requirements.md",
        file_metadata={},
    )
    session.add(file)
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(owner.id),
        json={
            "key": "project.requirements",
            "name": "Project requirements",
            "resource_type": "file_collection",
            "access_mode": "read",
            "locator": {"file_ids": [str(file.id)]},
            "parameter_schema": {
                "type": "object",
                "properties": {"section": {"type": "string"}},
            },
            "default_parameters": {"section": "overview"},
        },
    )
    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/catalog",
        headers=_headers(owner.id),
    )
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources/{created.json()['id']}",
        headers=_headers(owner.id),
        json={"description": "Approved project inputs"},
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources/"
        f"{created.json()['id']}/disable",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert created.json()["parameter_schema"]["additionalProperties"] is False
    assert any(tool["name"] == "read_workspace_file" for tool in catalog.json()["tools"])
    assert [item["key"] for item in catalog.json()["resources"]] == [
        "project.requirements"
    ]
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["version"] == 3
    actions = {
        event.action
        for event in session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == workspace.id)
        .all()
    }
    assert {
        "capability_resource.created",
        "capability_resource.updated",
        "capability_resource.disabled",
    } <= actions


def test_resource_rejects_foreign_locator_and_embedded_secret() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    other_owner, other_workspace = _seed_workspace(session, "other@example.com", "other")
    foreign_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        uploaded_by_user_id=other_owner.id,
        filename="private.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum_sha256="b" * 64,
        storage_key=f"workspaces/{other_workspace.id}/private.txt",
        file_metadata={},
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign memory team",
        team_type="delivery",
    )
    session.add_all([foreign_file, foreign_team])
    session.commit()

    foreign = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(owner.id),
        json={
            "key": "foreign.file",
            "name": "Foreign file",
            "resource_type": "file_collection",
            "locator": {"file_ids": [str(foreign_file.id)]},
        },
    )
    secret = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(owner.id),
        json={
            "key": "unsafe.memory",
            "name": "Unsafe memory",
            "resource_type": "memory_collection",
            "locator": {},
            "parameter_schema": {
                "type": "object",
                "properties": {"password": {"type": "string"}},
            },
        },
    )
    foreign_memory = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(owner.id),
        json={
            "key": "foreign.memory",
            "name": "Foreign memory",
            "resource_type": "memory_collection",
            "access_mode": "read",
            "locator": {
                "scope_types": ["team"],
                "scope_ids": [str(foreign_team.id)],
            },
        },
    )

    assert foreign.status_code == 422
    assert foreign.json()["error"]["code"] == "capability_configuration_invalid"
    assert secret.status_code == 422
    assert "credential reference" in secret.json()["error"]["message"]
    assert foreign_memory.status_code == 422
    assert foreign_memory.json()["error"]["code"] == "capability_configuration_invalid"


def test_viewer_can_read_but_cannot_manage_capability_resources() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    viewer = User(email="viewer@example.com", display_name="Viewer")
    session.add_all(
        [viewer, WorkspaceMember(workspace_id=workspace.id, user=viewer, role="viewer")]
    )
    session.commit()

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(viewer.id),
    )
    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(viewer.id),
        json={
            "key": "viewer.memory",
            "name": "Viewer memory",
            "resource_type": "memory_collection",
            "locator": {},
        },
    )

    assert owner.id
    assert listed.status_code == 200
    assert denied.status_code == 403


def test_effective_catalog_applies_team_department_and_locked_parameters() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="project.md",
        content_type="text/markdown",
        size_bytes=7,
        checksum_sha256="c" * 64,
        storage_key=f"workspaces/{workspace.id}/project.md",
        file_metadata={},
    )
    session.add(file)
    session.commit()
    created_resource = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/resources",
        headers=_headers(owner.id),
        json={
            "key": "project.files",
            "name": "Project files",
            "resource_type": "file_collection",
            "access_mode": "read",
            "locator": {"file_ids": [str(file.id)]},
            "parameter_schema": {
                "type": "object",
                "properties": {"section": {"type": "string"}},
            },
            "default_parameters": {"section": "overview"},
        },
    )
    resource_id = created_resource.json()["id"]
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        tool_policy={
            "allowed_tools": ["read_workspace_file", "write_artifact"],
        },
        capabilities={
            "resource_ids": [resource_id],
            "resource_parameters": {resource_id: {"section": "approved"}},
        },
    )
    team = AgentTeam(workspace_id=workspace.id, name="Delivery", team_type="delivery")
    session.add_all([agent, team])
    session.flush()
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="developer",
        department="Engineering",
    )
    session.add(member)
    session.commit()

    policy = client.put(
        f"/api/v1/workspaces/{workspace.id}/capabilities/teams/{team.id}/policy",
        headers=_headers(owner.id),
        json={
            "capability_policy": {
                "allowed_tools": ["read_workspace_file", "write_artifact"],
                "allowed_resource_ids": [resource_id],
                "departments": {
                    "Engineering": {
                        "allowed_tools": ["read_workspace_file"],
                        "allowed_resource_ids": [resource_id],
                        "resource_parameters": {
                            resource_id: {
                                "defaults": {"section": "approved"},
                                "locked": ["section"],
                            }
                        },
                    }
                },
            }
        },
    )
    effective = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/{agent.id}/effective-catalog",
        headers=_headers(owner.id),
        params={"team_id": str(team.id)},
    )
    repeated = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/{agent.id}/effective-catalog",
        headers=_headers(owner.id),
        params={"team_id": str(team.id)},
    )

    assert policy.status_code == 200
    assert policy.json()["capability_policy_version"] == 2
    assert effective.status_code == 200
    body = effective.json()
    assert [item["descriptor"]["name"] for item in body["tools"]] == [
        "read_workspace_file"
    ]
    assert body["resources"][0]["parameters"] == {"section": "approved"}
    assert body["resources"][0]["locked_parameters"] == ["section"]
    assert body["department"] == "Engineering"
    assert any(
        item["key"] == "write_artifact" and "department" in item["reason"]
        for item in body["denied"]
    )
    assert body["fingerprint"].startswith("sha256:")
    assert repeated.json()["fingerprint"] == body["fingerprint"]
    audit = session.query(AuditEvent).filter_by(
        workspace_id=workspace.id,
        action="team.capability_policy_updated",
    ).one()
    assert audit.audit_metadata["capability_policy_version"] == 2


def test_effective_catalog_requires_active_team_membership_and_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    other_owner, other_workspace = _seed_workspace(session, "other@example.com", "other")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Unassigned",
        role="developer",
        tool_policy={"allowed_tools": ["read_workspace_file"]},
    )
    team = AgentTeam(workspace_id=workspace.id, name="Restricted", team_type="delivery")
    foreign_agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Foreign",
        role="developer",
    )
    session.add_all([agent, team, foreign_agent])
    session.commit()

    not_member = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/{agent.id}/effective-catalog",
        headers=_headers(owner.id),
        params={"team_id": str(team.id)},
    )
    foreign = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/"
        f"{foreign_agent.id}/effective-catalog",
        headers=_headers(owner.id),
    )

    assert other_owner.id
    assert not_member.status_code == 403
    assert not_member.json()["error"]["code"] == "agent_team_membership_required"
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "agent_profile_not_found"


def test_effective_catalog_denies_ambiguous_mcp_tool_names() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "owner@example.com", "owner")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        tool_policy={"allowed_tools": ["search_docs"]},
    )
    servers = [
        McpServer(
            workspace_id=workspace.id,
            name=name,
            server_type="streamable_http",
            connection={"url": f"https://{name}.example.test/mcp"},
        )
        for name in ("primary", "secondary")
    ]
    session.add_all([agent, *servers])
    session.flush()
    session.add_all(
        [
            McpToolAllowlist(
                workspace_id=workspace.id,
                mcp_server_id=server.id,
                tool_name="search_docs",
                description="Search documentation",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
            for server in servers
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/{agent.id}/effective-catalog",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert response.json()["tools"] == []
    assert response.json()["denied"] == [
        {"kind": "tool", "key": "search_docs", "reason": "tool name is ambiguous"}
    ]


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = session_factory()
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            credential_encryption_secret="test-credential-secret",
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


def _seed_workspace(session: Session, email: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace


def _headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
