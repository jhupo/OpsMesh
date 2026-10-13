"""Native MCP product seeds; PostgreSQL is the durable authorization source."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.catalog.effective import effective_catalog_fingerprint
from opsmesh.capabilities.mcp.models import MCPCredentialReference, MCPServer, MCPToolAllowlist
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.runs.models import AgentRun, authorization_snapshot_fingerprint
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.workspaces.management.models import Workspace


def seed_mcp_run(
    session: Session,
    workspace: Workspace,
    *,
    snapshot_tools: list[str] | None = None,
    allow_policy: dict[str, object] | None = None,
    risk_level: str = "medium",
    requires_approval: bool = False,
) -> tuple[AgentRun, MCPServer]:
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Create poster",
    )
    session.add(task)
    session.flush()
    step = TaskStep(workspace_id=workspace.id, task_id=task.id, title="Generate image")
    session.add(step)
    session.flush()
    server = MCPServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
        health_status="healthy",
        last_health_check_at=datetime.now(UTC),
    )
    session.add(server)
    session.flush()
    allow = MCPToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        capability_key="image.generate",
        risk_level=risk_level,
        requires_approval=requires_approval,
        policy=allow_policy or {},
    )
    session.add(allow)
    session.flush()
    requested_tools = snapshot_tools or ["generate_image"]
    profile = AgentProfile(workspace_id=workspace.id, name="MCP fixture", role="expert")
    session.add(profile)
    session.flush()
    catalog: dict[str, object] = {
        "catalog_version": 1,
        "workspace_id": str(workspace.id),
        "agent_profile_id": str(profile.id),
        "agent_profile_version": 1,
        "team_id": None,
        "team_policy_version": None,
        "team_member_id": None,
        "department": None,
        "tools": [],
        "resources": [],
        "denied": [],
    }
    if "generate_image" in requested_tools:
        catalog["tools"] = [
            {
                "descriptor": {
                    "name": "generate_image",
                    "source": "mcp",
                    "description": allow.description,
                    "input_schema": allow.input_schema,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "capability_key": allow.capability_key,
                    "mcp_server_id": str(server.id),
                    "mcp_tool_allowlist_id": str(allow.id),
                    "mcp_server_name": server.name,
                    "mcp_server_type": server.server_type,
                    "mcp_server_configuration_version": server.configuration_version,
                    "mcp_tool_configuration_version": allow.configuration_version,
                    "mcp_requires_credentials": False,
                    "mcp_credential_references": [],
                    "mcp_blocked_reasons": [],
                    "policy": allow.policy,
                    "required_resource_type": None,
                    "required_access_modes": [],
                },
                "parameters": {},
                "locked_parameters": [],
                "provenance": [],
            }
        ]
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    snapshot: dict[str, object] = {
        "version": 4,
        "agent_profile_id": catalog["agent_profile_id"],
        "workspace_id": str(workspace.id),
        "allowed_tools": requested_tools,
        "capability_catalog": catalog,
        "runtime_policy": {
            "mcp": {
                "timeout_seconds": 15,
                "max_input_bytes": 64_000,
                "max_output_bytes": 256_000,
            }
        },
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status=RunStatus.RUNNING.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()
    return run, server


def bind_mcp_credential(
    session: Session,
    run: AgentRun,
    credential: MCPCredentialReference,
) -> None:
    snapshot = deepcopy(run.input["authorization_snapshot"])
    catalog = snapshot["capability_catalog"]
    descriptor = catalog["tools"][0]["descriptor"]
    server = session.get(MCPServer, UUID(str(descriptor["mcp_server_id"])))
    assert server is not None
    descriptor["mcp_server_type"] = server.server_type
    descriptor["mcp_server_configuration_version"] = server.configuration_version
    descriptor["mcp_credential_references"] = [
        {
            "credential_reference_id": str(credential.id),
            "mcp_server_id": (
                str(credential.mcp_server_id) if credential.mcp_server_id is not None else None
            ),
            "configuration_version": credential.configuration_version,
            "provider": credential.provider,
            "scopes": list(credential.scopes),
            "secret_fingerprint": credential.secret_fingerprint,
            "encryption_key_id": credential.encryption_key_id,
        }
    ]
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}
    session.commit()
