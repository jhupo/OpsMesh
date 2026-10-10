"""Worker-side transport to persistent MCP sessions inside shared Runtime hosts."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.execution.contracts import McpExecutionError
from opsmesh.capabilities.mcp.models import McpCredentialReference, McpDeployment, McpServer
from opsmesh.capabilities.mcp.transport.runtime_operation import RuntimeMcpOperation
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.contracts import DockerRuntimeClient, RuntimeCommandInputFile
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError


def deployment_runtime(session: Session, deployment: McpDeployment) -> WorkspaceRuntime | None:
    return session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == deployment.workspace_id,
            WorkspaceRuntime.id == deployment.runtime_id,
            WorkspaceRuntime.execution_mode.in_(["pooled", "persistent"]),
            WorkspaceRuntime.execution_run_id.is_(None),
            WorkspaceRuntime.runtime_provider == "cloud_docker",
        )
    )


def require_managed_host(
    session: Session, workspace_id: UUID, runtime_id: UUID
) -> WorkspaceRuntime:
    runtime = session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.id == runtime_id,
        )
    )
    if runtime is None:
        raise NotFoundError("Runtime host not found")
    if (
        runtime.execution_mode not in {"pooled", "persistent"}
        or runtime.execution_run_id is not None
        or runtime.runtime_provider != "cloud_docker"
        or runtime.status != "running"
        or runtime.connection_status != "online"
        or not runtime.docker_container_id
        or runtime.capabilities.get("plugin_install_id")
    ):
        raise PolicyDeniedError("MCP requires an available shared Runtime host")
    return runtime


def process_request(
    docker: DockerRuntimeClient,
    runtime: WorkspaceRuntime,
    server_id: UUID,
    payload: dict[str, object],
    *,
    timeout_seconds: int = 70,
) -> dict[str, object]:
    if runtime.status != "running" or not runtime.docker_container_id:
        raise McpExecutionError("Managed MCP runtime is not running", code="mcp_process_not_ready")
    record = docker.exec_command(
        runtime.docker_container_id,
        ["python", "-m", "opsmesh_runtime.mcp_process"],
        timeout_seconds=timeout_seconds,
        input_file=RuntimeCommandInputFile(
            content=json.dumps({**payload, "id": str(server_id)}).encode(),
            argument_name="--request-file",
        ),
    )
    try:
        result = json.loads(record.stdout)
    except (ValueError, TypeError):
        result = None
    if record.exit_code or not isinstance(result, dict) or "error" in result:
        raise McpExecutionError(
            "Managed MCP process request failed", code="mcp_process_request_failed"
        )
    return result


class ManagedMcpToolAdapter:
    def __init__(self, session: Session, docker: DockerRuntimeClient) -> None:
        self.session = session
        self.docker = docker

    def prepare(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> RuntimeMcpOperation:
        deployment = self.session.scalar(
            select(McpDeployment).where(
                McpDeployment.workspace_id == server.workspace_id,
                McpDeployment.mcp_server_id == server.id,
            )
        )
        if (
            deployment is None
            or not (
                deployment.status == "running"
                or (deployment.action == "refresh" and deployment.status in {"queued", "starting"})
            )
            or server.status != "active"
            or server.platform_blocked
            or deployment.server_version != server.configuration_version
        ):
            raise McpExecutionError(
                "Managed MCP process requires start or refresh", code="mcp_process_not_ready"
            )
        if deployment.credential_version is not None:
            bound = str(server.connection.get("credential_reference_id"))
            credentials = [c for c in credential_refs if str(c.id) == bound]
            if (
                len(credentials) != 1
                or credentials[0].configuration_version != deployment.credential_version
            ):
                raise McpExecutionError(
                    "Restart MCP after credential rotation", code="mcp_process_credentials_stale"
                )
        runtime = deployment_runtime(self.session, deployment)
        if runtime is None:
            raise McpExecutionError(
                "Managed MCP runtime is unavailable", code="mcp_process_not_ready"
            )
        if RuntimeAllocationStore(self.session).get(runtime, "mcp", deployment.id) is None:
            raise McpExecutionError(
                "Managed MCP has no execution slot", code="mcp_process_not_ready"
            )
        if not runtime.docker_container_id:
            raise McpExecutionError(
                "Managed Runtime has no container", code="mcp_process_not_ready"
            )
        return RuntimeMcpOperation(
            self.docker,
            runtime.docker_container_id,
            "managed",
            {
                "action": "call",
                "name": tool_name,
                "arguments": arguments,
                "timeout_seconds": min(timeout_seconds, 50),
            },
            min(timeout_seconds, 70),
            server_id=str(server.id),
        )
