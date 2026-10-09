"""Worker-side transport to persistent MCP sessions inside dedicated runtimes."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.capabilities.mcp.models import McpCredentialReference, McpDeployment, McpServer
from backend.app.capabilities.mcp.transport.payloads import result_from_sdk_output
from backend.app.runtime.instances.contracts import DockerRuntimeClient, RuntimeCommandInputFile
from backend.app.runtime.instances.models import WorkspaceRuntime


def deployment_runtime(session: Session, deployment: McpDeployment) -> WorkspaceRuntime | None:
    return session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == deployment.workspace_id,
            WorkspaceRuntime.id == deployment.runtime_id,
            WorkspaceRuntime.execution_mode == "persistent",
            WorkspaceRuntime.runtime_provider == "cloud_docker",
        )
    )


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

    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
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
        self.session.commit()
        result = await asyncio.to_thread(
            process_request,
            self.docker,
            runtime,
            server.id,
            {
                "action": "call",
                "name": tool_name,
                "arguments": arguments,
                "timeout_seconds": min(timeout_seconds, 50),
            },
            timeout_seconds=min(timeout_seconds, 70),
        )
        return result_from_sdk_output(json.dumps(result))
