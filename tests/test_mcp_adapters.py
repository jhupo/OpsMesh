"""Native SDK transport requirements and credential boundaries."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.catalog.discovery import (
    MCPDiscoveryConflictError,
    MCPDiscoveryError,
    MCPToolDiscoveryService,
)
from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionError
from opsmesh.capabilities.mcp.managed_schemas import StdioProjectConfig
from opsmesh.capabilities.mcp.models import MCPCredentialReference, MCPServer, MCPToolAllowlist
from opsmesh.capabilities.mcp.transport.payloads import stdio_command
from opsmesh.capabilities.mcp.transport.stdio_credentials import _merge_environment
from opsmesh.governance.audit.models import AuditEvent
from tests.fixtures.agent_product import _seed_workspace
from tests.fixtures.database import product_session


@pytest.mark.parametrize(
    "connection",
    [
        {"command": ""},
        {"command": ["python"]},
        {"command": "python", "args": "--version"},
        {"command": "python", "args": [1]},
    ],
)
def test_stdio_rejects_noncanonical_command(connection):
    with pytest.raises((ValueError, MCPExecutionError)):
        stdio_command(connection)


@pytest.mark.parametrize(
    "values",
    [
        {"command": "python", "args": ["C:/Users/private.py"]},
        {"command": "python", "cwd": "relative"},
        {"command": "python", "env": {"BAD-NAME": "canary"}},
    ],
)
def test_managed_config_rejects_invalid_runtime_paths_and_credentials(values):
    with pytest.raises(ValueError):
        StdioProjectConfig.model_validate(values)


def test_credential_environment_merge_rejects_duplicates_and_excessive_payload():
    with pytest.raises(MCPExecutionError):
        _merge_environment({"KEY": "first"}, {"KEY": "second"})
    with pytest.raises(MCPExecutionError):
        _merge_environment({}, {"KEY": "x" * 65537})


@pytest.mark.parametrize("conflict", ["server", "credential", "duplicate_names"])
def test_worker_discovery_rejects_stale_or_duplicate_results_without_replacing_reviewed_tools(
    conflict: str,
) -> None:
    """Worker publication keeps approved tool state and audit on rejected discovery."""
    with product_session() as session:
        user, workspace = _seed_workspace(session)
        server = MCPServer(workspace_id=workspace.id, name="tool-catalog", server_type="stdio")
        session.add(server)
        session.flush()
        credential = MCPCredentialReference(
            workspace_id=workspace.id,
            mcp_server_id=server.id,
            name="tool-credential",
            provider="hosted",
            external_ref="",
        )
        session.add(credential)
        session.commit()
        service = MCPToolDiscoveryService(session)
        original_tools = [{"name": "fetch_logs", "inputSchema": {"type": "object"}}]
        initial = service.persist_runtime_tools(
            workspace.id,
            server,
            original_tools,
            credential_id=credential.id,
            credential_version=credential.configuration_version,
            actor_user_id=user.id,
            configuration_version=server.configuration_version,
        )
        reviewed_tool = session.scalar(select(MCPToolAllowlist))
        assert reviewed_tool is not None
        reviewed_tool.status = "active"
        session.commit()
        expected_server_version = server.configuration_version
        expected_credential_version = credential.configuration_version
        original_audit_ids = set(session.scalars(select(AuditEvent.id)))
        with Session(session.get_bind()) as concurrent_session:
            if conflict == "server":
                current_server = concurrent_session.get(MCPServer, server.id)
                assert current_server is not None
                current_server.configuration_version += 1
            elif conflict == "credential":
                current_credential = concurrent_session.get(MCPCredentialReference, credential.id)
                assert current_credential is not None
                current_credential.configuration_version += 1
            concurrent_session.commit()
        incoming = [{"name": "new_tool", "inputSchema": {"type": "object"}}]
        if conflict == "duplicate_names":
            incoming.append({"name": " new_tool ", "inputSchema": {"type": "object"}})
        expected_error = (
            MCPDiscoveryError if conflict == "duplicate_names" else MCPDiscoveryConflictError
        )
        with pytest.raises(expected_error):
            service.persist_runtime_tools(
                workspace.id,
                server,
                incoming,
                credential_id=credential.id,
                credential_version=expected_credential_version,
                actor_user_id=user.id,
                configuration_version=expected_server_version,
            )
        session.rollback()
        with Session(session.get_bind()) as observer:
            stored_server = observer.get(MCPServer, server.id)
            assert stored_server is not None
            assert stored_server.discovery_version == initial["discovery_version"]
            assert stored_server.discovery_checksum == initial["discovery_checksum"]
            stored_tools = list(observer.scalars(select(MCPToolAllowlist)))
            assert [(tool.tool_name, tool.status) for tool in stored_tools] == [
                ("fetch_logs", "active")
            ]
            assert set(observer.scalars(select(AuditEvent.id))) == original_audit_ids
