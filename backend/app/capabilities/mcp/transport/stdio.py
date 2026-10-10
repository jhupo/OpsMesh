from __future__ import annotations

from uuid import UUID

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError, McpExecutionPending
from backend.app.capabilities.mcp.models import McpCredentialReference, McpServer
from backend.app.capabilities.mcp.transport.payloads import (
    MCP_PYTHON_SDK_PACKAGE,
    MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
    MCP_STDIO_CONTRACT_VERSION,
    stdio_command,
    stdio_sdk_request,
)
from backend.app.capabilities.mcp.transport.runtime_operation import (
    CompletedMcpOperation,
    RuntimeMcpOperation,
)
from backend.app.capabilities.mcp.transport.stdio_credentials import (
    hosted_stdio_environment,
    self_hosted_stdio_environment_refs,
)
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.models import WorkspaceRuntime
from backend.app.runtime.self_hosted.dispatch.mcp import SelfHostedMcpJobService
from backend.app.shared.security.secrets import SecretEncryptionService


class DockerRuntimeStdioMcpToolAdapter:
    def __init__(
        self,
        *,
        docker: DockerRuntimeClient,
        runtime: WorkspaceRuntime,
        secret_service: SecretEncryptionService | None = None,
        working_dir: str | None = None,
    ) -> None:
        self._docker, self._runtime, self._secret_service = docker, runtime, secret_service
        self._working_dir = working_dir

    def prepare(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> RuntimeMcpOperation:
        if not self._runtime.docker_container_id:
            raise McpExecutionError("Runtime has no container", code="mcp_runtime_unavailable")
        request = stdio_sdk_request(
            command=stdio_command(server.connection),
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            environment=hosted_stdio_environment(
                credential_refs, secret_service=self._secret_service
            ),
        )
        return RuntimeMcpOperation(
            self._docker,
            self._runtime.docker_container_id,
            "stdio",
            request,
            timeout_seconds,
            self._working_dir or "/",
        )


class SelfHostedStdioMcpToolAdapter:
    def __init__(
        self,
        *,
        service: SelfHostedMcpJobService,
        runtime: WorkspaceRuntime,
        agent_run_id: UUID,
        tool_call_id: object = None,
    ) -> None:
        self._service = service
        self._runtime = runtime
        self._agent_run_id = agent_run_id
        self._tool_call_id = tool_call_id if isinstance(tool_call_id, str) else None

    def prepare(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> CompletedMcpOperation:
        command = stdio_command(server.connection)
        request = stdio_sdk_request(
            command=command,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )
        payload: dict[str, object] = {
            "contract_version": MCP_STDIO_CONTRACT_VERSION,
            "transport": "stdio",
            "sdk": {
                "package": MCP_PYTHON_SDK_PACKAGE,
                "entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
            },
            "request": request,
            "environment_refs": self_hosted_stdio_environment_refs(credential_refs),
        }
        job = self._service.create_mcp_job(
            workspace_id=server.workspace_id,
            runtime_id=self._runtime.id,
            agent_run_id=self._agent_run_id,
            mcp_server_id=server.id,
            tool_name=tool_name,
            request_payload=payload,
            tool_call_id=self._tool_call_id,
        )
        if job.status == "completed":
            if job.response_payload is None:
                raise McpExecutionError("Runtime RPC has no result", code="mcp_rpc_result_missing")
            return CompletedMcpOperation(job.response_payload)
        if job.status in {"failed", "expired"}:
            raise McpExecutionError("Runtime RPC failed", code="mcp_rpc_failed")
        raise McpExecutionPending(
            "MCP tool is queued for self-hosted runtime execution",
            code="mcp_self_hosted_job_queued",
            response={
                "mcp_job_id": str(job.id),
                "runtime_id": str(self._runtime.id),
                "transport": "stdio",
            },
        )
