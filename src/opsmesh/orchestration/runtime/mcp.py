"""Independently authorized MCP records and native transport construction."""

import asyncio
import shutil
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from agents.mcp import MCPServer, MCPServerSse, MCPServerStdio, MCPServerStreamableHttp
from mcp.types import CallToolResult, GetPromptResult, ListPromptsResult, Tool
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.orchestration.contracts import (
    ExecutionScope,
    ExecutionSubject,
    ScopeAdmission,
    SharedResourceAdmission,
)
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.models import MCPConnection
from opsmesh.orchestration.runtime.sandbox import SandboxBackend
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.http.client import PinnedAsyncHTTPTransport
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest


class MCPMeter(Protocol):
    def assert_connection_idle(self, session: Session, connection_id: UUID) -> None: ...

    async def claim(
        self, context: dict[str, Any], name: str, arguments: dict[str, Any] | None
    ) -> tuple[bool, dict[str, Any]]: ...
    async def complete(
        self, context: dict[str, Any], result: dict[str, Any] | None
    ) -> dict[str, Any] | None: ...


def connection_scope(record: MCPConnection) -> ExecutionScope:
    return ExecutionScope(
        configuration_id=None if record.owner_ref.startswith("user:") else record.scope_id,
    )


def load_connection(
    session: Session,
    *,
    connection_id: UUID,
    principal_id: UUID,
    admission: ScopeAdmission,
    shared_use: bool = False,
) -> MCPConnection:
    record = session.get(MCPConnection, connection_id)
    if record is None:
        raise NotFoundError("The MCP connection does not exist")
    if record.principal_id != principal_id:
        if not shared_use or not isinstance(admission, SharedResourceAdmission):
            raise NotFoundError("The MCP connection does not exist")
        admission.admit_resource_use(
            session,
            subject=ExecutionSubject(principal_id),
            scope=connection_scope(record),
            kind="mcp",
            resource_id=record.id,
            owner_user_id=record.principal_id,
        )
    admission.admit(session, subject=ExecutionSubject(principal_id), scope=connection_scope(record))
    return record


class MCPServerFactory:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        admission: ScopeAdmission,
        cipher: SecretCipher,
        backend: SandboxBackend,
        meter: MCPMeter,
    ) -> None:
        self.sessions = sessions
        self.admission = admission
        self.cipher = cipher
        self.backend = backend
        self.meter = meter

    def __call__(self, context: dict[str, Any]) -> "AuthorizedMCPServer":
        return AuthorizedMCPServer(self, context)

    def assert_connection_idle(self, session: Session, connection_id: UUID) -> None:
        self.meter.assert_connection_idle(session, connection_id)

    async def native(self, context: dict[str, Any]) -> MCPServer:
        connection_id, principal_id = UUID(context["connection_id"]), UUID(context["principal_id"])
        with self.sessions() as session:
            record = load_connection(
                session,
                connection_id=connection_id,
                principal_id=principal_id,
                admission=self.admission,
                shared_use=context.get("lifecycle") is None,
            )
            lifecycle = context.get("lifecycle")
            if record.status != "ready" and lifecycle not in {"connect", "discover", "close"}:
                raise ConflictError("The MCP connection is not ready")
            transport, endpoint = record.transport, record.endpoint
            configuration: dict[str, Any] = dict(record.configuration)
            headers: dict[str, str] = {}
            environment_values = dict(configuration.get("environment", {}))
            state = None
            if transport == "stdio":
                if record.environment_id is None:
                    raise ConflictError("Stdio MCP requires an explicit environment")
                environment = load_environment(
                    session,
                    environment_id=record.environment_id,
                    subject=ExecutionSubject(principal_id),
                    admission=self.admission,
                    shared_use=context.get("lifecycle") is None,
                )
                if environment.status != "ready" or environment.active_operation_id is not None:
                    raise ConflictError("The MCP environment is unavailable")
                if (
                    environment.scope_id != record.scope_id
                    or environment.owner_ref != record.owner_ref
                ):
                    raise ForbiddenError("The MCP environment belongs to a different scope")
                state = self.cipher.decrypt_payload(environment.encrypted_state or "")
        if transport == "stdio":
            assert state is not None
            native = await self.backend.resume(state)
            container_id = self.backend.state(native).get("container_id")
            executable = shutil.which("docker")
            if not executable or not isinstance(container_id, str):
                raise ConflictError("The approved sandbox transport is unavailable")
            command = configuration.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(arg, str) for arg in command)
            ):
                raise ConflictError("The MCP command is not a valid argument vector")
            return MCPServerStdio(
                params={
                    "command": executable,
                    "args": [
                        "exec",
                        "-i",
                        *[
                            argument
                            for name in sorted(environment_values)
                            for argument in ("-e", name)
                        ],
                        container_id,
                        *command,
                    ],
                    "env": {**environment_values, "PATH": str(Path(executable).parent)},
                },
                max_retry_attempts=0,
            )
        if endpoint is None:
            raise ConflictError("Remote MCP requires an endpoint")
        parts = urlsplit(endpoint)
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ForbiddenError("MCP endpoints must not contain credentials, queries or fragments")

        def http_client(**kwargs: Any) -> httpx2.AsyncClient:
            kwargs.pop("follow_redirects", None)
            kwargs.pop("trust_env", None)
            kwargs.pop("transport", None)
            return httpx2.AsyncClient(
                **kwargs,
                transport=PinnedAsyncHTTPTransport(endpoint),
                follow_redirects=False,
                trust_env=False,
            )

        params: Any = {"url": endpoint, "headers": headers, "httpx_client_factory": http_client}
        if transport == "sse":
            return MCPServerSse(params=params, max_retry_attempts=0)
        return MCPServerStreamableHttp(params=params, max_retry_attempts=0)


class AuthorizedMCPServer(MCPServer):
    def __init__(self, factory: MCPServerFactory, context: dict[str, Any]) -> None:
        super().__init__()
        self.factory, self.context = factory, context
        self.server: MCPServer | None = None

    @property
    def name(self) -> str:
        return "opsmesh"

    async def connect(self) -> None:
        self.server = await self.factory.native(self.context)
        await cast(Any, self.server).connect()

    def connected(self) -> MCPServer:
        if self.server is None:
            raise ConflictError("The native MCP transport is disconnected")
        return self.server

    async def cleanup(self) -> None:
        if self.server is not None:
            await cast(Any, self.server).cleanup()
            self.server = None

    async def list_tools(self, run_context: Any = None, agent: Any = None) -> list[Tool]:
        tools = await self.connected().list_tools(run_context, agent)
        digest = payload_digest(
            {
                "tools": sorted(
                    [tool.model_dump(mode="json") for tool in tools], key=lambda item: item["name"]
                )
            }
        )
        if self.context.get("tools_digest", digest) != digest:
            raise ConflictError("The MCP tool manifest changed; bind its new version explicitly")
        return tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: Any = None
    ) -> CallToolResult:
        if "run_id" not in self.context:
            raise ForbiddenError("MCP tool effects require a currently admitted Run")
        tools = await self.list_tools()
        if tool_name not in self.context.get("allowed_tools", []) or not any(
            tool.name == tool_name for tool in tools
        ):
            raise ForbiddenError("The MCP tool is not part of the frozen allowed manifest")
        call_context = dict(self.context)
        claimed, cached = await self.factory.meter.claim(call_context, tool_name, arguments)
        if not claimed:
            return CallToolResult.model_validate(cached)
        try:
            result = await self.connected().call_tool(tool_name, arguments, meta)
            effective = await self.factory.meter.complete(
                call_context, result.model_dump(mode="json")
            )
            return CallToolResult.model_validate(effective) if effective is not None else result
        except BaseException:
            await asyncio.shield(self.factory.meter.complete(call_context, None))
            raise

    async def list_prompts(self) -> ListPromptsResult:
        return await self.connected().list_prompts()

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        return await self.connected().get_prompt(name, arguments)
