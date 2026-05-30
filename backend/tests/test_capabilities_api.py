from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import McpCredentialReference, McpServer, McpToolCallLog, Skill
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
    assert "external_ref" not in credential.json()
    assert credential.json()["external_ref_configured"] is True
    assert credential.json()["external_ref_kind"] == "secret"
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
    assert "external_ref" not in body
    assert body["external_ref_configured"] is False
    assert body["external_ref_kind"] is None
    assert body["secret_fingerprint"].startswith("sha256:")
    assert body["encryption_key_id"] == "test"
    assert "secret_payload" not in body
    assert "encrypted_secret_payload" not in body

    stored = session.query(McpCredentialReference).filter_by(name="hosted-key").one()
    assert stored.encrypted_secret_payload is not None
    assert "sk-secret" not in stored.encrypted_secret_payload
    assert stored.secret_fingerprint == body["secret_fingerprint"]


def test_mcp_credentials_can_be_listed_filtered_and_disabled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "hosted-tools",
            "server_type": "hosted",
            "connection": {"transport": "http_jsonrpc", "url": "https://mcp.example.test/rpc"},
        },
    )
    other_server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={"name": "other-tools"},
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "name": "image-key",
            "provider": "hosted",
            "secret_payload": {"api_key": "sk-test"},
        },
    )
    denied_foreign_filter = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials"
        f"?mcp_server_id={other_server.json()['id']}",
        headers=_headers(owner.id),
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials"
        f"?mcp_server_id={server.json()['id']}",
        headers=_headers(owner.id),
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials/"
        f"{credential.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    active_after_disable = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials",
        headers=_headers(owner.id),
    )
    all_after_disable = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials?include_disabled=true",
        headers=_headers(owner.id),
    )
    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert other_server.status_code == 201
    assert allowed.status_code == 201
    assert credential.status_code == 201
    assert denied_foreign_filter.status_code == 404
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["name"] == "image-key"
    assert "external_ref" not in listed.json()["items"][0]
    assert listed.json()["items"][0]["external_ref_configured"] is False
    assert listed.json()["items"][0]["secret_fingerprint"] is not None
    assert "encrypted_secret_payload" not in listed.json()["items"][0]
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert active_after_disable.json()["items"] == []
    assert all_after_disable.json()["items"][0]["status"] == "disabled"
    assert catalog.json()["items"][0]["credential_status"] == "missing_required"
    assert "missing_required_credentials" in catalog.json()["items"][0]["blocked_reasons"]


def test_mcp_server_response_redacts_connection_secrets_and_url_details() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "remote-tools",
            "server_type": "http_jsonrpc",
            "connection": {
                "url": "https://mcp.example.test/private/rpc?token=secret",
                "headers": {"Authorization": "Bearer secret-token"},
                "nested": {"api_key": "sk-secret"},
                "transport": "http_jsonrpc",
            },
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    assert "private/rpc" not in str(created.json())
    assert "secret" not in str(created.json())
    assert created.json()["connection"] == {
        "url_configured": True,
        "url_host": "mcp.example.test",
        "headers": "[redacted]",
        "nested": {"api_key": "[redacted]"},
        "transport": "http_jsonrpc",
    }
    assert listed.status_code == 200
    assert listed.json()["items"][0]["connection"]["url_host"] == "mcp.example.test"


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


def test_mcp_catalog_summarizes_tools_credentials_and_agent_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    image_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "hosted",
            "connection": {
                "transport": "http_jsonrpc",
                "url": "https://mcp.example.test/rpc",
                "requires_credentials": True,
            },
            "visibility": "private",
        },
    )
    research_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "research-tools",
            "server_type": "sse",
            "connection": {"url": "https://research.example.test/sse"},
            "visibility": "public",
        },
    )
    foreign_server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={"name": "foreign-tools"},
    )
    assert image_server.status_code == 201
    assert research_server.status_code == 201
    assert foreign_server.status_code == 201

    image_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{image_server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "medium",
        },
    )
    research_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{research_server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "search_web", "capability_key": "web.search"},
    )
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-credentials",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": image_server.json()["id"],
            "name": "image-key",
            "provider": "hosted",
            "secret_payload": {"api_key": "sk-test"},
        },
    )
    assert image_tool.status_code == 201
    assert research_tool.status_code == 201
    assert credential.status_code == 201

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
    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )
    scoped_catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog"
        f"?agent_profile_id={agent.json()['id']}",
        headers=_headers(owner.id),
    )

    assert agent.status_code == 201
    assert catalog.status_code == 200
    by_name = {item["name"]: item for item in catalog.json()["items"]}
    assert set(by_name) == {"image-tools", "research-tools"}
    assert by_name["image-tools"]["execution_mode"] == "hosted"
    assert by_name["image-tools"]["credential_status"] == "server_configured"
    assert by_name["image-tools"]["credential_count"] == 1
    assert by_name["image-tools"]["executable"] is True
    assert by_name["image-tools"]["connection_summary"] == {
        "requires_credentials": True,
        "transport": "http_jsonrpc",
        "remote_host": "mcp.example.test",
        "has_remote_url": True,
        "has_stdio_command": False,
    }
    assert by_name["image-tools"]["tools"][0]["tool_name"] == "generate_image"
    assert by_name["image-tools"]["tools"][0]["requires_approval"] is True
    assert by_name["research-tools"]["execution_mode"] == "remote_sse"
    assert by_name["research-tools"]["credential_status"] == "not_required"
    assert by_name["research-tools"]["tools"][0]["tool_name"] == "search_web"
    assert scoped_catalog.status_code == 200
    scoped_by_name = {item["name"]: item for item in scoped_catalog.json()["items"]}
    assert [tool["tool_name"] for tool in scoped_by_name["image-tools"]["tools"]] == [
        "generate_image"
    ]
    assert scoped_by_name["research-tools"]["tools"] == []
    assert scoped_by_name["research-tools"]["executable"] is False
    assert "no_allowed_tools" in scoped_by_name["research-tools"]["blocked_reasons"]


def test_mcp_catalog_flags_missing_required_credentials() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "hosted-tools",
            "server_type": "hosted",
            "connection": {"transport": "http_jsonrpc", "url": "https://example.test/mcp"},
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "create_asset"},
    )
    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert allowed.status_code == 201
    body = catalog.json()["items"][0]
    assert body["credential_status"] == "missing_required"
    assert body["executable"] is False
    assert "missing_required_credentials" in body["blocked_reasons"]


def test_mcp_catalog_and_policy_diagnostics_block_stale_health_checks() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "remote-tools",
            "server_type": "http_jsonrpc",
            "connection": {"url": "https://mcp.example.test/rpc"},
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "search_docs"},
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Researcher",
            "role": "researcher",
            "tool_policy": {"mcp_tools": ["search_docs"]},
        },
    )
    stored_server = session.get(McpServer, UUID(server.json()["id"]))
    assert stored_server is not None
    stored_server.health_status = "healthy"
    stored_server.last_health_check_at = datetime(2026, 1, 1, tzinfo=UTC)
    session.commit()

    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )
    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/"
        f"{agent.json()['id']}/tool-policy-diagnostics",
        headers=_headers(owner.id),
    )
    governance = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/governance",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert allowed.status_code == 201
    assert agent.status_code == 201
    assert catalog.status_code == 200
    catalog_item = catalog.json()["items"][0]
    assert catalog_item["health_status"] == "healthy"
    assert catalog_item["executable"] is False
    assert catalog_item["blocked_reasons"] == ["health_check_stale"]
    assert diagnostics.status_code == 200
    tool = diagnostics.json()["effective_tools"][0]
    assert tool["tool_name"] == "search_docs"
    assert tool["available"] is False
    assert tool["blocked_reasons"] == ["health_check_stale"]
    assert governance.status_code == 200
    body = governance.json()
    assert body["summary"]["blocked_reason_counts"]["health_check_stale"] == 1
    assert body["summary"]["blocked_reason_counts"]["unavailable_allowed_mcp_tools"] == 1
    assert body["mcp_servers"][0]["blocked_reasons"] == ["health_check_stale"]

    refreshed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/health-check",
        headers=_headers(owner.id),
        json={"health_status": "healthy", "error_code": "token-like-value-is-not-stored"},
    )
    refreshed_catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )
    audit = session.query(AuditEvent).filter_by(
        workspace_id=workspace.id,
        action="mcp_server.health_check_recorded",
    ).one()

    assert refreshed.status_code == 200
    refreshed_body = refreshed.json()
    assert refreshed_body["health_status"] == "healthy"
    assert refreshed_body["last_health_check_at"] is not None
    assert refreshed_body["last_error"] is None
    refreshed_item = refreshed_catalog.json()["items"][0]
    assert refreshed_item["executable"] is True
    assert refreshed_item["blocked_reasons"] == []
    assert audit.audit_metadata["health_status"] == "healthy"
    assert audit.audit_metadata["last_error_configured"] is False
    assert audit.audit_metadata["error_code"] is None
    assert "token-like-value-is-not-stored" not in str(audit.audit_metadata)
    assert "token-like-value-is-not-stored" not in str(refreshed_body)


def test_mcp_catalog_includes_tool_and_server_usage_rollups() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "http_jsonrpc",
            "connection": {"url": "https://mcp.example.test/rpc"},
        },
    )
    first_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    second_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "upscale_image"},
    )
    succeeded = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "generate_image",
            "status": "completed",
            "request": {"arguments_sha256": "args-ok"},
            "response": {"result": {"ok": True}},
        },
    )
    failed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "upscale_image",
            "status": "failed",
            "request": {"arguments_sha256": "args-fail"},
            "error": {"code": "mcp_remote_error", "message": "Remote tool failed"},
        },
    )

    catalog = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert first_tool.status_code == 201
    assert second_tool.status_code == 201
    assert succeeded.status_code == 201
    assert failed.status_code == 201
    assert catalog.status_code == 200
    body = catalog.json()["items"][0]
    assert body["usage"]["call_count"] == 2
    assert body["usage"]["failed_call_count"] == 1
    assert body["usage"]["last_call_status"] == "failed"
    assert body["usage"]["last_error_code"] == "mcp_remote_error"
    tools = {tool["tool_name"]: tool for tool in body["tools"]}
    assert tools["generate_image"]["usage"]["call_count"] == 1
    assert tools["generate_image"]["usage"]["failed_call_count"] == 0
    assert tools["generate_image"]["usage"]["last_call_status"] == "completed"
    assert tools["upscale_image"]["usage"]["call_count"] == 1
    assert tools["upscale_image"]["usage"]["failed_call_count"] == 1
    assert tools["upscale_image"]["usage"]["last_error_code"] == "mcp_remote_error"


def test_mcp_server_and_tool_can_be_disabled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "http_jsonrpc",
            "connection": {"url": "https://mcp.example.test/rpc"},
        },
    )
    first_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    second_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "upscale_image"},
    )
    disabled_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools/"
        f"{first_tool.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    tools_after_disable = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tools",
        headers=_headers(owner.id),
    )
    catalog_after_tool_disable = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )
    disabled_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    catalog_after_server_disable = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-catalog",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert first_tool.status_code == 201
    assert second_tool.status_code == 201
    assert disabled_tool.status_code == 200
    assert disabled_tool.json()["status"] == "disabled"
    assert tools_after_disable.status_code == 200
    assert [item["tool_name"] for item in tools_after_disable.json()] == ["upscale_image"]
    assert [
        item["tool_name"] for item in catalog_after_tool_disable.json()["items"][0]["tools"]
    ] == ["upscale_image"]
    assert disabled_server.status_code == 200
    assert disabled_server.json()["status"] == "disabled"
    assert catalog_after_server_disable.status_code == 200
    body = catalog_after_server_disable.json()["items"][0]
    assert body["status"] == "disabled"
    assert body["executable"] is False
    assert "server_inactive" in body["blocked_reasons"]


def test_mcp_tool_call_logs_can_be_listed_and_filtered() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={"name": "image-tools", "connection": {"command": "mcp-image"}},
    )
    other_server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={"name": "other-tools"},
    )
    first_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    second_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "upscale_image"},
    )
    completed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "generate_image",
            "status": "completed",
            "request": {
                "arguments_sha256": "ok",
                "api_key": "sk-secret",
                "nested": {"authorization": "Bearer secret-token"},
            },
            "response": {"result": {"ok": True, "token": "secret-token"}},
        },
    )
    failed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "upscale_image",
            "status": "failed",
            "request": {"arguments_sha256": "failed"},
            "error": {"code": "mcp_remote_error"},
        },
    )
    all_logs = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
    )
    failed_logs = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs?status=failed",
        headers=_headers(owner.id),
    )
    tool_logs = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs"
        "?tool_name=generate_image",
        headers=_headers(owner.id),
    )
    server_logs = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs"
        f"?mcp_server_id={server.json()['id']}",
        headers=_headers(owner.id),
    )
    denied_foreign_filter = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs"
        f"?mcp_server_id={other_server.json()['id']}",
        headers=_headers(owner.id),
    )

    assert server.status_code == 201
    assert other_server.status_code == 201
    assert first_tool.status_code == 201
    assert second_tool.status_code == 201
    assert completed.status_code == 201
    assert completed.json()["request"]["api_key"] == "[redacted]"
    assert completed.json()["request"]["nested"]["authorization"] == "[redacted]"
    assert completed.json()["response"]["result"]["token"] == "[redacted]"
    assert failed.status_code == 201
    assert all_logs.status_code == 200
    assert all_logs.json()["total"] == 2
    assert all_logs.json()["items"][0]["status"] == "failed"
    assert failed_logs.status_code == 200
    assert failed_logs.json()["total"] == 1
    assert failed_logs.json()["items"][0]["error_code"] == "mcp_remote_error"
    assert tool_logs.status_code == 200
    assert tool_logs.json()["total"] == 1
    assert tool_logs.json()["items"][0]["tool_name"] == "generate_image"
    assert tool_logs.json()["items"][0]["request"]["api_key"] == "[redacted]"
    assert tool_logs.json()["items"][0]["response"]["result"]["token"] == "[redacted]"
    assert server_logs.status_code == 200
    assert server_logs.json()["total"] == 2
    assert denied_foreign_filter.status_code == 404


def test_mcp_tool_call_log_rejects_foreign_server_reference() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    local_server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={"name": "local-tools"},
    )
    local_tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{local_server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={"tool_name": "generate_image"},
    )
    foreign_server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={"name": "foreign-tools"},
    )
    foreign_tool = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers/"
        f"{foreign_server.json()['id']}/tools",
        headers=_headers(other.id),
        json={"tool_name": "generate_image"},
    )
    forged_log = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": foreign_server.json()["id"],
            "tool_name": "generate_image",
            "status": "completed",
            "request": {"arguments_sha256": "forged"},
            "response": {"result": {"ok": True}},
        },
    )

    assert local_server.status_code == 201
    assert local_tool.status_code == 201
    assert foreign_server.status_code == 201
    assert foreign_tool.status_code == 201
    assert forged_log.status_code == 404
    assert session.query(McpToolCallLog).count() == 0


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


def test_private_skills_are_only_visible_to_owner_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    private_skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={"key": "private-writer", "name": "Private Writer", "visibility": "private"},
    )
    public_skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={"key": "public-writer", "name": "Public Writer", "visibility": "public"},
    )
    assert private_skill.status_code == 201
    assert public_skill.status_code == 201

    owner_skills = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
    )
    other_skills = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/skills",
        headers=_headers(other.id),
    )
    private_install_denied = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": private_skill.json()["id"]},
    )
    public_install = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": public_skill.json()["id"]},
    )

    assert {item["key"] for item in owner_skills.json()["items"]} >= {
        "private-writer",
        "public-writer",
    }
    assert {item["key"] for item in other_skills.json()["items"]} == {"public-writer"}
    assert private_install_denied.status_code == 404
    assert public_install.status_code == 201


def test_workspace_skill_install_snapshots_public_skill_source() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "poster-maker",
            "name": "Poster Maker",
            "version": "1.2.0",
            "description": "Create posters",
            "capability_keys": ["image.generate"],
            "manifest": {"tools": ["generate_image"], "prompt": "v1"},
            "visibility": "public",
        },
    )
    assert created.status_code == 201

    installed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": created.json()["id"], "config": {"quality": "high"}},
    )
    source_skill = session.get(Skill, UUID(created.json()["id"]))
    assert source_skill is not None
    source_skill.name = "Poster Maker Changed"
    source_skill.manifest = {"tools": ["generate_image"], "prompt": "v2"}
    session.commit()

    listed = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
    )
    body = listed.json()["items"][0]

    assert installed.status_code == 201
    assert installed.json()["installed_key"] == "poster-maker"
    assert installed.json()["installed_name"] == "Poster Maker"
    assert installed.json()["installed_version"] == "1.2.0"
    assert installed.json()["installed_capability_keys"] == ["image.generate"]
    assert installed.json()["installed_manifest"] == {
        "tools": ["generate_image"],
        "prompt": "v1",
    }
    assert installed.json()["source_checksum"].startswith("sha256:")
    assert body["installed_name"] == "Poster Maker"
    assert body["installed_manifest"] == {"tools": ["generate_image"], "prompt": "v1"}
    assert body["config"] == {"quality": "high"}


def test_workspace_skill_availability_reports_required_mcp_tool_state() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "image-pack",
            "name": "Image Pack",
            "version": "1.0.0",
            "manifest": {
                "mcp_tools": [
                    {"tool_name": "generate_image"},
                    "upscale_image",
                    "upscale_image",
                ]
            },
            "visibility": "public",
        },
    )
    installed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": skill.json()["id"]},
    )
    server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={
            "name": "image-tools",
            "server_type": "hosted",
            "connection": {
                "transport": "http_jsonrpc",
                "url": "https://mcp.example.test/rpc",
            },
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(other.id),
        json={"tool_name": "generate_image", "capability_key": "image.generate"},
    )
    missing_tool_availability = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/availability",
        headers=_headers(other.id),
    )
    second_allowed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(other.id),
        json={"tool_name": "upscale_image", "capability_key": "image.upscale"},
    )
    credential = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-credentials",
        headers=_headers(other.id),
        json={
            "mcp_server_id": server.json()["id"],
            "name": "image-key",
            "provider": "hosted",
            "secret_payload": {"api_key": "sk-test"},
        },
    )
    usable_availability = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/availability",
        headers=_headers(other.id),
    )
    disabled = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/disable",
        headers=_headers(other.id),
    )
    disabled_availability = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/availability",
        headers=_headers(other.id),
    )
    foreign_availability = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/availability",
        headers=_headers(owner.id),
    )

    assert skill.status_code == 201
    assert installed.status_code == 201
    assert server.status_code == 201
    assert allowed.status_code == 201
    assert missing_tool_availability.status_code == 200
    missing_body = missing_tool_availability.json()
    assert missing_body["required_tools"] == ["generate_image", "upscale_image"]
    assert missing_body["usable"] is False
    assert "missing_required_mcp_tools" in missing_body["blocked_reasons"]
    tools = {item["tool_name"]: item for item in missing_body["tools"]}
    assert tools["generate_image"]["blocked_reasons"] == ["missing_required_credentials"]
    assert tools["upscale_image"]["blocked_reasons"] == ["tool_not_allowed"]
    assert second_allowed.status_code == 201
    assert credential.status_code == 201
    assert usable_availability.status_code == 200
    usable_body = usable_availability.json()
    assert usable_body["usable"] is True
    assert usable_body["blocked_reasons"] == []
    assert all(item["available"] for item in usable_body["tools"])
    assert disabled.status_code == 200
    assert disabled_availability.status_code == 200
    assert disabled_availability.json()["usable"] is False
    assert "skill_install_disabled" in disabled_availability.json()["blocked_reasons"]
    assert foreign_availability.status_code == 404


def test_agent_tool_policy_diagnostics_explain_skill_and_mcp_effective_access() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "image-pack",
            "name": "Image Pack",
            "version": "1.0.0",
            "manifest": {"mcp_tools": ["generate_image", "upscale_image"]},
            "visibility": "public",
        },
    )
    installed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/workspace-skills",
        headers=_headers(owner.id),
        json={"skill_id": skill.json()["id"]},
    )
    foreign_install = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": skill.json()["id"]},
    )
    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "hosted",
            "connection": {
                "transport": "http_jsonrpc",
                "url": "https://mcp.example.test/private?token=hidden",
            },
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "medium",
        },
    )
    missing_install_id = str(uuid4())
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Designer",
            "role": "designer",
            "skills": {
                "installed_skill_ids": [
                    installed.json()["id"],
                    foreign_install.json()["id"],
                    missing_install_id,
                ]
            },
            "tool_policy": {"mcp_tools": ["generate_image", "delete_image"]},
        },
    )
    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/agents/"
        f"{agent.json()['id']}/tool-policy-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_diagnostics = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/agents/"
        f"{agent.json()['id']}/tool-policy-diagnostics",
        headers=_headers(other.id),
    )

    assert skill.status_code == 201
    assert installed.status_code == 201
    assert foreign_install.status_code == 201
    assert server.status_code == 201
    assert allowed.status_code == 201
    assert agent.status_code == 201
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert body["policy_mode"] == "allowlist"
    assert body["configured_mcp_tools"] == ["delete_image", "generate_image"]
    assert body["missing_policy_tools"] == ["delete_image"]
    assert body["missing_agent_skill_install_ids"] == [
        foreign_install.json()["id"],
        missing_install_id,
    ]
    assert body["installed_skills"] == [
        {
            "install_id": installed.json()["id"],
            "installed_key": "image-pack",
            "installed_name": "Image Pack",
            "installed_version": "1.0.0",
            "source_visibility": "public",
            "status": "active",
            "usable": False,
            "required_tools": ["generate_image", "upscale_image"],
            "blocked_reasons": ["missing_required_mcp_tools"],
        }
    ]
    tools = {item["tool_name"]: item for item in body["effective_tools"]}
    assert tools["generate_image"]["allowed_by_agent_policy"] is True
    assert tools["generate_image"]["allowed_in_workspace"] is True
    assert tools["generate_image"]["available"] is False
    assert tools["generate_image"]["credential_status"] == "missing_required"
    assert tools["generate_image"]["execution_mode"] == "hosted"
    assert tools["generate_image"]["requires_approval"] is True
    assert tools["generate_image"]["risk_level"] == "medium"
    assert tools["generate_image"]["blocked_reasons"] == ["missing_required_credentials"]
    assert tools["upscale_image"]["allowed_by_agent_policy"] is False
    assert tools["upscale_image"]["allowed_in_workspace"] is False
    assert tools["upscale_image"]["available"] is False
    assert set(tools["upscale_image"]["blocked_reasons"]) == {
        "tool_not_allowed",
        "not_allowed_by_agent_policy",
    }
    assert {
        "missing_agent_skill_installs",
        "configured_mcp_tools_not_allowed",
        "unusable_installed_skills",
        "unavailable_allowed_mcp_tools",
    } <= set(body["blocked_reasons"])
    assert "hidden" not in str(body)
    assert "private" not in str(body)
    assert foreign_diagnostics.status_code == 404


def test_workspace_tool_policy_matrix_summarizes_agent_tool_access() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, _ = _seed_workspace(session, email="other-matrix@example.com", slug="other-matrix")

    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "hosted",
            "connection": {
                "transport": "http_jsonrpc",
                "url": "https://mcp.example.test/private?token=hidden",
                "requires_credentials": True,
            },
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "high",
        },
    )
    designer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Designer",
            "role": "designer",
            "tool_policy": {"mcp_tools": ["generate_image"]},
            "model_settings": {"api_key": "sk-designer"},
        },
    )
    reviewer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Reviewer",
            "role": "reviewer",
            "tool_policy": {"mcp_tools": ["delete_image"]},
        },
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/tool-policy-matrix",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/tool-policy-matrix",
        headers=_headers(other.id),
    )

    assert server.status_code == 201
    assert allowed.status_code == 201
    assert designer.status_code == 201
    assert reviewer.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["tool_names"] == ["delete_image", "generate_image"]
    assert body["summary"] == {
        "agent_count": 2,
        "blocked_agent_count": 2,
        "tool_name_count": 2,
        "unavailable_tool_count": 2,
        "policy_modes": {"allowlist": 2},
        "blocked_reasons": {
            "configured_mcp_tools_not_allowed": 1,
            "unavailable_allowed_mcp_tools": 2,
        },
    }
    agents = {item["agent_name"]: item for item in body["agents"]}
    designer_tools = {item["tool_name"]: item for item in agents["Designer"]["effective_tools"]}
    reviewer_tools = {item["tool_name"]: item for item in agents["Reviewer"]["effective_tools"]}
    assert designer_tools["generate_image"]["allowed_by_agent_policy"] is True
    assert designer_tools["generate_image"]["available"] is False
    assert designer_tools["generate_image"]["credential_status"] == "missing_required"
    assert designer_tools["generate_image"]["blocked_reasons"] == [
        "missing_required_credentials"
    ]
    assert reviewer_tools["delete_image"]["allowed_by_agent_policy"] is True
    assert reviewer_tools["delete_image"]["allowed_in_workspace"] is False
    assert set(reviewer_tools["delete_image"]["blocked_reasons"]) == {"tool_not_allowed"}
    assert forbidden.status_code == 403
    serialized = str(body)
    assert "hidden" not in serialized
    assert "private" not in serialized
    assert "sk-designer" not in serialized


def test_workspace_capability_governance_summarizes_skill_agent_and_mcp_risk() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "image-pack",
            "name": "Image Pack",
            "version": "1.0.0",
            "manifest": {
                "mcp_tools": [
                    "generate_image",
                    "upscale_image",
                ],
                "api_key": "sk-skill",
            },
            "visibility": "public",
        },
    )
    installed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/workspace-skills",
        headers=_headers(owner.id),
        json={"skill_id": skill.json()["id"], "config": {"token": "hidden-token"}},
    )
    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=_headers(owner.id),
        json={
            "name": "image-tools",
            "server_type": "hosted",
            "connection": {
                "transport": "http_jsonrpc",
                "url": "https://mcp.example.test/private?token=hidden",
                "requires_credentials": True,
            },
        },
    )
    allowed = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/"
        f"{server.json()['id']}/tools",
        headers=_headers(owner.id),
        json={
            "tool_name": "generate_image",
            "capability_key": "image.generate",
            "requires_approval": True,
            "risk_level": "high",
        },
    )
    failed_log = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs",
        headers=_headers(owner.id),
        json={
            "mcp_server_id": server.json()["id"],
            "tool_name": "generate_image",
            "status": "failed",
            "request": {"arguments_sha256": "args", "token": "request-token"},
            "error": {"code": "timeout", "api_key": "sk-error"},
        },
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Designer",
            "role": "designer",
            "skills": {"installed_skill_ids": [installed.json()["id"]]},
            "tool_policy": {"mcp_tools": ["generate_image", "delete_image"]},
            "model_settings": {"api_key": "sk-agent"},
        },
    )
    foreign_server = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/mcp-servers",
        headers=_headers(other.id),
        json={
            "name": "foreign-tools",
            "server_type": "http_jsonrpc",
            "connection": {"url": "https://foreign.example.test/private"},
        },
    )

    governance = client.get(
        f"/api/v1/workspaces/{workspace.id}/capabilities/governance",
        headers=_headers(owner.id),
    )

    assert skill.status_code == 201
    assert installed.status_code == 201
    assert server.status_code == 201
    assert allowed.status_code == 201
    assert failed_log.status_code == 201
    assert agent.status_code == 201
    assert foreign_server.status_code == 201
    assert governance.status_code == 200
    body = governance.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["summary"] == {
        "skill_installs": 1,
        "active_skill_installs": 1,
        "unusable_skill_installs": 1,
        "agents": 1,
        "agents_with_blocks": 1,
        "mcp_servers": 1,
        "executable_mcp_servers": 0,
        "blocked_mcp_servers": 1,
        "allowed_mcp_tools": 1,
        "high_risk_mcp_tools": 1,
        "tools_requiring_approval": 1,
        "missing_required_credential_servers": 1,
        "failed_mcp_tool_calls": 1,
        "catalog_truncated": False,
        "blocked_reason_counts": {
            "configured_mcp_tools_not_allowed": 1,
            "missing_required_credentials": 1,
            "missing_required_mcp_tools": 1,
            "unavailable_allowed_mcp_tools": 1,
            "unusable_installed_skills": 1,
        },
    }
    assert body["skills"] == [
        {
            "install_id": installed.json()["id"],
            "installed_key": "image-pack",
            "installed_name": "Image Pack",
            "installed_version": "1.0.0",
            "status": "active",
            "usable": False,
            "required_tools": ["generate_image", "upscale_image"],
            "blocked_reasons": ["missing_required_mcp_tools"],
        }
    ]
    assert body["agents"] == [
        {
            "agent_profile_id": agent.json()["id"],
            "name": "Designer",
            "role": "designer",
            "status": "active",
            "policy_mode": "allowlist",
            "configured_mcp_tools": ["delete_image", "generate_image"],
            "installed_skill_count": 1,
            "effective_tool_count": 3,
            "unavailable_tool_count": 2,
            "blocked_reasons": [
                "configured_mcp_tools_not_allowed",
                "unusable_installed_skills",
                "unavailable_allowed_mcp_tools",
            ],
        }
    ]
    assert body["mcp_servers"] == [
        {
            "server_id": server.json()["id"],
            "name": "image-tools",
            "server_type": "hosted",
            "status": "active",
            "health_status": "unknown",
            "execution_mode": "hosted",
            "executable": False,
            "credential_status": "missing_required",
            "allowed_tool_count": 1,
            "high_risk_tool_count": 1,
            "approval_required_tool_count": 1,
            "failed_call_count": 1,
            "blocked_reasons": ["missing_required_credentials"],
        }
    ]
    serialized = str(body)
    assert "sk-skill" not in serialized
    assert "hidden-token" not in serialized
    assert "private" not in serialized
    assert "request-token" not in serialized
    assert "sk-error" not in serialized
    assert "sk-agent" not in serialized
    assert "foreign-tools" not in serialized
    assert "foreign.example.test" not in serialized


def test_workspace_skill_install_can_upgrade_and_disable_without_source_access() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    v1 = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "writer",
            "name": "Writer",
            "version": "1.0.0",
            "manifest": {"prompt": "v1"},
            "visibility": "public",
        },
    )
    v2 = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "writer",
            "name": "Writer Pro",
            "version": "2.0.0",
            "manifest": {"prompt": "v2"},
            "visibility": "public",
        },
    )
    installed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
        json={"skill_id": v1.json()["id"], "config": {"tone": "clear"}},
    )
    agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Writer Agent",
        role="writer",
        skills={"installed_skill_ids": [installed.json()["id"]]},
        tool_policy={"mcp_tools": ["draft_text"]},
    )
    session.add(agent)
    session.commit()

    impact = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/impact?target_skill_id={v2.json()['id']}",
        headers=_headers(other.id),
    )
    upgraded = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/upgrade",
        headers=_headers(other.id),
        json={"skill_id": v2.json()["id"], "config": {"tone": "bold"}},
    )
    rollback = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/rollback",
        headers=_headers(other.id),
        json={},
    )
    disabled = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/disable",
        headers=_headers(other.id),
    )
    listed = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
    )
    foreign_disable = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/disable",
        headers=_headers(owner.id),
    )

    assert impact.status_code == 200
    impact_body = impact.json()
    assert impact_body["install_id"] == installed.json()["id"]
    assert impact_body["current_version"] == "1.0.0"
    assert impact_body["target_version"] == "2.0.0"
    assert impact_body["affected_agent_count"] == 1
    assert impact_body["affected_agents"][0]["agent_profile_id"] == str(agent.id)
    assert impact_body["blocked_reasons"] == []
    assert upgraded.status_code == 200
    assert upgraded.json()["installed_name"] == "Writer Pro"
    assert upgraded.json()["installed_version"] == "2.0.0"
    assert upgraded.json()["installed_manifest"] == {"prompt": "v2"}
    assert upgraded.json()["config"] == {"tone": "bold"}
    assert upgraded.json()["disabled_at"] is None
    assert "_lifecycle" not in upgraded.json()["config"]
    assert rollback.status_code == 200
    assert rollback.json()["installed_name"] == "Writer"
    assert rollback.json()["installed_version"] == "1.0.0"
    assert rollback.json()["installed_manifest"] == {"prompt": "v1"}
    assert rollback.json()["config"] == {"tone": "bold"}
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["disabled_at"] is not None
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert foreign_disable.status_code == 404

    audit = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/audit-events",
        headers=_headers(other.id),
    )
    actions = {item["action"] for item in audit.json()["items"]}
    assert {
        "skill_install.upgraded",
        "skill_install.rolled_back",
        "skill_install.disabled",
    } <= actions


def test_skill_install_by_id_endpoint_and_disabled_history() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    skill = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/skills",
        headers=_headers(owner.id),
        json={
            "key": "research-pack",
            "name": "Research Pack",
            "version": "1.0.0",
            "manifest": {"tools": ["search_web"]},
            "visibility": "public",
        },
    )
    installed = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/skills/"
        f"{skill.json()['id']}/install",
        headers=_headers(other.id),
        json={"config": {"region": "sg"}},
    )
    disabled = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills/"
        f"{installed.json()['id']}/disable",
        headers=_headers(other.id),
    )
    active_list = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills",
        headers=_headers(other.id),
    )
    history_list = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/workspace-skills"
        "?include_disabled=true",
        headers=_headers(other.id),
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/capabilities/skills/"
        f"{skill.json()['id']}/install",
        headers=_headers(other.id),
        json={},
    )

    assert installed.status_code == 201
    assert installed.json()["installed_key"] == "research-pack"
    assert installed.json()["config"] == {"region": "sg"}
    assert installed.json()["disabled_at"] is None
    assert disabled.status_code == 200
    assert disabled.json()["disabled_at"] is not None
    assert active_list.status_code == 200
    assert active_list.json()["items"] == []
    assert history_list.status_code == 200
    assert history_list.json()["items"][0]["status"] == "disabled"
    assert history_list.json()["items"][0]["disabled_at"] == disabled.json()["disabled_at"]
    assert duplicate.status_code == 409


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
