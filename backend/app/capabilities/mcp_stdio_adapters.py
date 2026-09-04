from __future__ import annotations

import json
from uuid import UUID

from backend.app.capabilities.mcp_adapter_payloads import (
    MCP_PYTHON_SDK_PACKAGE,
    MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
    capability_report_from_sdk_output,
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
        self.assert_sdk_ready(workspace_id=server.workspace_id)
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
                "opsmesh_runtime.mcp_stdio_client",
                json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            ],
        )
        if record.status != "completed" or record.exit_code != 0:
            raise McpExecutionError(
                "Docker runtime MCP stdio command failed",
                code="mcp_stdio_runtime_failed",
            )
        return result_from_sdk_output(record.stdout)

    def assert_sdk_ready(self, *, workspace_id: UUID) -> None:
        cached_report = (self._runtime.capabilities or {}).get("mcp_stdio_sdk")
        if _sdk_report_is_ready(cached_report):
            return
        record = self._runtime_manager.execute_command(
            workspace_id=workspace_id,
            runtime=self._runtime,
            command=[
                "python",
                "-m",
                "opsmesh_runtime.mcp_stdio_client",
                "--check",
            ],
        )
        if record.status != "completed" or record.exit_code != 0:
            raise McpExecutionError(
                "Docker runtime does not provide the official MCP Python SDK",
                code="mcp_stdio_sdk_unavailable",
            )
        try:
            report = capability_report_from_sdk_output(record.stdout)
        except McpExecutionError as exc:
            raise McpExecutionError(
                "Docker runtime MCP SDK capability probe returned invalid output",
                code="mcp_stdio_runtime_not_ready",
            ) from exc
        if not _sdk_report_is_ready(report):
            raise McpExecutionError(
                "Docker runtime MCP SDK is not ready",
                code="mcp_stdio_runtime_not_ready",
            )
        self._runtime.capabilities = {
            **dict(self._runtime.capabilities or {}),
            "mcp_stdio_sdk": report,
        }


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


def _sdk_report_is_ready(report: object) -> bool:
    return (
        isinstance(report, dict)
        and report.get("status") == "ready"
        and report.get("contract_version") == 1
        and report.get("sdk_package") == MCP_PYTHON_SDK_PACKAGE
        and report.get("stdio_client") == "available"
        and report.get("client_session") == "available"
    )
