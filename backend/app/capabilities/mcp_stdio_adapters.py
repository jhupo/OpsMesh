from __future__ import annotations

import json
from uuid import UUID

from backend.app.capabilities.mcp_adapter_payloads import (
    MCP_PYTHON_SDK_PACKAGE,
    MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
    result_from_sdk_output,
    stdio_command,
    stdio_sdk_request,
)
from backend.app.capabilities.mcp_execution_types import (
    McpExecutionError,
    McpExecutionPending,
)
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.mcp_jobs import SelfHostedMcpJobService


class DockerRuntimeStdioMcpToolAdapter:
    def __init__(self, *, runtime_manager: RuntimeManager, runtime: WorkspaceRuntime) -> None:
        self._runtime_manager = runtime_manager
        self._runtime = runtime

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        _ = credential_refs
        command = stdio_command(server.connection)
        request = stdio_sdk_request(
            command=command,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )
        record = self._runtime_manager.execute_command(
            workspace_id=server.workspace_id,
            runtime=self._runtime,
            command=[
                "python",
                "-m",
                "backend.app.runtime_manager.mcp_stdio_client",
                json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            ],
        )
        if record.status != "completed" or record.exit_code != 0:
            raise McpExecutionError(
                "Docker runtime MCP stdio command failed",
                code="mcp_stdio_runtime_failed",
            )
        return result_from_sdk_output(record.stdout)


class SelfHostedStdioMcpToolAdapter:
    def __init__(
        self,
        *,
        service: SelfHostedMcpJobService,
        runtime: WorkspaceRuntime,
        agent_run_id: UUID,
    ) -> None:
        self._service = service
        self._runtime = runtime
        self._agent_run_id = agent_run_id

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        _ = credential_refs
        command = stdio_command(server.connection)
        request = stdio_sdk_request(
            command=command,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )
        payload: dict[str, object] = {
            "transport": "stdio",
            "sdk": {
                "package": MCP_PYTHON_SDK_PACKAGE,
                "entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
            },
            "request": request,
        }
        job = self._service.create_mcp_job(
            workspace_id=server.workspace_id,
            runtime_id=self._runtime.id,
            agent_run_id=self._agent_run_id,
            mcp_server_id=server.id,
            tool_name=tool_name,
            request_payload=payload,
        )
        raise McpExecutionPending(
            "MCP tool is queued for self-hosted runtime execution",
            code="mcp_self_hosted_job_queued",
            response={
                "mcp_job_id": str(job.id),
                "runtime_id": str(self._runtime.id),
                "transport": "stdio",
            },
        )
