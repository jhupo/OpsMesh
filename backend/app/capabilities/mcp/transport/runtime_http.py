"""Send native SDK remote MCP execution into an already-authorized Docker Runtime."""

import json

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.capabilities.mcp.models import McpCredentialReference, McpServer
from backend.app.capabilities.mcp.transport.payloads import (
    result_from_sdk_output,
    string_dict_setting,
)
from backend.app.capabilities.mcp.transport.remote import (
    credential_headers,
    validate_mcp_auth_headers,
    validate_mcp_url,
)
from backend.app.runtime.instances.contracts import RuntimeCommandInputFile
from backend.app.runtime.instances.manager import RuntimeManager
from backend.app.runtime.instances.models import WorkspaceRuntime
from backend.app.shared.security.egress import MCP_EGRESS_URL_POLICY
from backend.app.shared.security.secrets import SecretEncryptionService


class DockerRuntimeHttpMcpToolAdapter:
    def __init__(
        self,
        manager: RuntimeManager,
        runtime: WorkspaceRuntime,
        secrets: SecretEncryptionService | None,
    ) -> None:
        self.manager, self.runtime, self.secrets = manager, runtime, secrets

    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        url = server.connection.get("url") or server.connection.get("endpoint")
        declared_transport = (
            server.connection.get("transport")
            if server.server_type == "hosted"
            else server.server_type
        )
        if declared_transport not in {"sse", "streamable_http"}:
            raise McpExecutionError(
                "Unsupported Runtime MCP transport", code="mcp_hosted_transport_unsupported"
            )
        transport = "sse" if declared_transport == "sse" else "http"
        if not isinstance(url, str):
            raise McpExecutionError("Remote MCP is missing its URL", code="mcp_server_url_missing")
        validate_mcp_url(url, egress_policy=MCP_EGRESS_URL_POLICY, transport=transport)
        headers = {
            **string_dict_setting(server.connection, "headers"),
            **credential_headers(credential_refs, secret_service=self.secrets),
        }
        validate_mcp_auth_headers(server, headers)
        request = {
            "contract_version": 2,
            "transport": transport,
            "server": {
                "url": url,
                "headers": headers,
                "timeout": timeout_seconds,
                "sse_read_timeout": timeout_seconds,
            },
            "tool": {"name": tool_name, "arguments": arguments, "timeout_seconds": timeout_seconds},
        }
        record = await self.manager.execute_command_async(
            workspace_id=server.workspace_id,
            runtime=self.runtime,
            command=["python", "-m", "opsmesh_runtime.mcp_http_client"],
            input_file=RuntimeCommandInputFile(
                content=json.dumps(request).encode(), argument_name="--request-file"
            ),
        )
        if record.status != "completed" or record.exit_code != 0:
            raise McpExecutionError("Runtime MCP HTTP call failed", code="mcp_runtime_http_failed")
        return result_from_sdk_output(record.stdout)
