"""Send native SDK remote MCP execution into an already-authorized Docker Runtime."""

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.capabilities.mcp.models import McpCredentialReference, McpServer
from backend.app.capabilities.mcp.transport.payloads import (
    string_dict_setting,
)
from backend.app.capabilities.mcp.transport.remote import (
    credential_headers,
    validate_mcp_auth_headers,
    validate_mcp_url,
)
from backend.app.capabilities.mcp.transport.runtime_operation import RuntimeMcpOperation
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.models import WorkspaceRuntime
from backend.app.shared.security.egress import MCP_EGRESS_URL_POLICY
from backend.app.shared.security.secrets import SecretEncryptionService


class DockerRuntimeHttpMcpToolAdapter:
    def __init__(
        self,
        docker: DockerRuntimeClient,
        runtime: WorkspaceRuntime,
        secrets: SecretEncryptionService | None,
    ) -> None:
        self.docker, self.runtime, self.secrets = docker, runtime, secrets

    def prepare(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> RuntimeMcpOperation:
        url = server.connection.get("url")
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
        if not self.runtime.docker_container_id:
            raise McpExecutionError("Runtime has no container", code="mcp_runtime_unavailable")
        return RuntimeMcpOperation(
            self.docker, self.runtime.docker_container_id, "http", request, timeout_seconds
        )
