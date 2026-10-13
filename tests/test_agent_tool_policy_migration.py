"""Retired wildcard migration cannot grant tools across tenants or future tools."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile, AgentProfileVersion
from opsmesh.capabilities.mcp.models import MCPServer, MCPToolAllowlist
from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema


def test_tool_policy_migration_limits_wildcards_and_preserves_explicit_grants() -> None:
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0135_native_plugin_deployments")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Policy owner")
            workspace = Workspace(owner=user, name="Policy", slug=uuid4().hex)
            foreign = Workspace(owner=user, name="Foreign policy", slug=uuid4().hex)
            session.add_all([user, workspace, foreign])
            session.flush()
            server = MCPServer(workspace_id=workspace.id, name="owned", connection={})
            foreign_server = MCPServer(workspace_id=foreign.id, name="foreign", connection={})
            session.add_all([server, foreign_server])
            session.flush()
            session.add_all(
                [
                    MCPToolAllowlist(
                        workspace_id=workspace.id, mcp_server_id=server.id, tool_name="query"
                    ),
                    MCPToolAllowlist(
                        workspace_id=workspace.id,
                        mcp_server_id=server.id,
                        tool_name="blocked",
                        platform_blocked=True,
                    ),
                    MCPToolAllowlist(
                        workspace_id=foreign.id,
                        mcp_server_id=foreign_server.id,
                        tool_name="foreign_query",
                    ),
                ]
            )
            agent = AgentProfile(
                workspace_id=workspace.id,
                name="Migrated policy",
                role="expert",
                tool_policy={"mcp_tools": "*", "require_approval": True},
            )
            explicit = AgentProfile(
                workspace_id=workspace.id,
                name="Explicit policy",
                role="expert",
                tool_policy={"mcp_tools": "*", "allowed_tools": ["memory.list"]},
            )
            session.add_all([agent, explicit])
            session.flush()
            version = AgentProfileVersion(
                workspace_id=workspace.id,
                agent_profile_id=agent.id,
                version=1,
                snapshot={"name": agent.name, "tool_policy": dict(agent.tool_policy)},
            )
            session.add(version)
            session.commit()
            agent_id, explicit_id, version_id = agent.id, explicit.id, version.id
            workspace_id, server_id = workspace.id, server.id
        command.upgrade(config, "0136_agent_tool_allowlist")
        with Session(engine) as session:
            migrated = session.get(AgentProfile, agent_id)
            preserved = session.get(AgentProfile, explicit_id)
            historical = session.get(AgentProfileVersion, version_id)
            assert migrated is not None and preserved is not None and historical is not None
            assert migrated.tool_policy == {"allowed_tools": ["query"], "require_approval": True}
            assert preserved.tool_policy == {"allowed_tools": ["memory.list"]}
            assert historical.snapshot["tool_policy"] == migrated.tool_policy
            session.add(
                MCPToolAllowlist(
                    workspace_id=workspace_id,
                    mcp_server_id=server_id,
                    tool_name="added_after_upgrade",
                )
            )
            session.commit()
            session.refresh(migrated)
            assert migrated.tool_policy["allowed_tools"] == ["query"]
        command.downgrade(config, "0135_native_plugin_deployments")
        with Session(engine) as session:
            restored = session.get(AgentProfile, agent_id)
            assert restored is not None
            assert restored.tool_policy == {"mcp_tools": ["query"], "require_approval": True}
