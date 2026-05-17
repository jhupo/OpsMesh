from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.capabilities.models import McpCredentialReference, McpToolCallLog
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_capability_skill_and_mcp_control_plane() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    capability = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities",
        headers=_headers(owner.id),
        json={
            "key": "image.generate",
            "name": "图片生成",
            "category": "creative",
            "default_policy": {"requires_review": True},
        },
    )
    assert capability.status_code == 201

    skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "poster-maker",
            "name": "海报制作",
            "capability_keys": ["image.generate"],
            "manifest": {"runtime": "docker"},
        },
    )
    assert skill.status_code == 201

    install = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/workspace-skills",
        headers=_headers(owner.id),
        json={"skill_id": skill.json()["id"], "config": {"quality": "high"}},
    )
    assert install.status_code == 201

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "stdio",
            "connection": {"command": "mcp-image"},
        },
    )
    assert server.status_code == 201

    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "medium",
            "policy": {"write_artifact": True},
        },
    )
    assert allowed.status_code == 201
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "name": "image-api-key",
            "provider": "vault",
            "external_ref": "secret/image-api-key",
            "scopes": ["images.write"],
        },
    )
    assert credential.status_code == 201
    assert credential.json()["secret_fingerprint"] is None
    assert credential.json()["encryption_key_id"] is None
    blocked_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "delete_image", "risk_level": "high"},
    )
    assert blocked_tool.status_code == 201

    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Designer",
            "role": "designer",
            "instructions": "Create visual assets.",
            "tool_policy": {"mcp_tools": ["generate_image"]},
        },
    )
    assert agent.status_code == 201

    mapped = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tools"
        f"?agent_profile_id={agent.json()['id']}",
        headers=_headers(owner.id),
    )
    assert mapped.status_code == 200
    assert mapped.json() == [
        {
            "server_id": server.json()["id"],
            "server_name": "image-tools",
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "medium",
            "policy": {"write_artifact": True},
        }
    ]

    logged = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "generate_image",
            "status": "waiting_approval",
            "request": {"prompt": "mountain"},
        },
    )
    assert logged.status_code == 201
    assert session.query(McpToolCallLog).count() == 1

    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-events",
        headers=_headers(owner.id),
    )
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert {
        "skill.installed",
        "mcp_server.created",
        "mcp_tool.allowed",
        "mcp_credential.created",
        "agent.created",
    } <= actions


def test_hosted_mcp_credentials_are_encrypted_and_not_returned() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={"name": "hosted-tools"},
    )
    assert server.status_code == 201

    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "name": "hosted-key",
            "provider": "hosted",
            "secret_payload": {"api_key": "sk-secret", "base_url": "https://example.test"},
            "scopes": ["images.write"],
        },
    )

    assert credential.status_code == 201
    body = credential.json()
    assert body["provider"] == "hosted"
    assert body["external_ref"] == ""
    assert body["secret_fingerprint"].startswith("sha256:")
    assert body["encryption_key_id"] == "test"
    assert "secret_payload" not in body
    assert "encrypted_secret_payload" not in body

    stored = session.query(McpCredentialReference).filter_by(name="hosted-key").one()
    assert stored.encrypted_secret_payload is not None
    assert "sk-secret" not in stored.encrypted_secret_payload
    assert stored.secret_fingerprint == body["secret_fingerprint"]


def test_mcp_server_scope_is_enforced() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={"name": "other-tools"},
    )
    assert server.status_code == 201

    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "steal_data"},
    )

    assert denied.status_code == 404


def test_operator_cannot_manage_capabilities() -> None:
    client, session = _client()
    operator, workspace = _seed_workspace(session, role="operator")

    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(operator.id),
        json={"name": "operator-tools"},
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(operator.id),
    )

    assert denied.status_code == 403
    assert listed.status_code == 200


def test_capability_conflicts_return_409_and_keep_session_usable() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    first_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={"name": "image-tools"},
    )
    duplicate_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={"name": "image-tools"},
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{first_server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    duplicate_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{first_server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    mapped = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tools",
        headers=_headers(owner.id),
    )

    assert first_server.status_code == 201
    assert duplicate_server.status_code == 409
    assert duplicate_server.json()["error"]["code"] == "conflict"
    assert duplicate_server.json()["error"]["message"] == "MCP server name already exists"
    assert allowed.status_code == 201
    assert duplicate_tool.status_code == 409
    assert duplicate_tool.json()["error"]["message"] == (
        "MCP tool is already allowed for this server"
    )
    assert mapped.status_code == 200
    assert len(mapped.json()) == 1


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
            credential_encryption_secret="test-credential-secret",
            credential_encryption_key_id="test",
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
    slug: str = "owner",
    role: str = "owner",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
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
