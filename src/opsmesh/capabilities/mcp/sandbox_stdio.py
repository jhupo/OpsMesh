"""Native MCP stdio transport running user commands inside SDK-owned sandboxes."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

from agents.mcp import MCPServerStdio
from agents.mcp.server import MCPStreamTransport
from agents.mcp.util import create_static_tool_filter
from agents.sandbox import Manifest
from agents.sandbox.sandboxes.docker import DockerSandboxSessionState
from mcp.client.session import MessageHandlerFnT
from mcp.client.stdio import stdio_client
from sqlalchemy.orm import Session

from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService


class SandboxMCPServerStdio(MCPServerStdio):
    """Use the SDK protocol client with Docker's trusted duplex exec transport.

    SandboxClient exposes command execution, but not a duplex stdio stream. Docker exec
    supplies that missing transport; lifecycle and persisted state remain SDK-owned.
    Each client owns one sandbox, so cancellation cannot terminate another invocation.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractContextManager[Session]],
        backends: SandboxBackendRegistry,
        secrets: SecretEncryptionService,
        workspace_id: UUID,
        runtime_id: UUID,
        server_id: UUID,
        command: list[str],
        environment: dict[str, str],
        cwd: str | None,
        name: str,
        timeout_seconds: float,
        tool_names: tuple[str, ...] | None = None,
        message_handler: MessageHandlerFnT | None = None,
    ) -> None:
        executable = shutil.which("docker")
        if executable is None or not os.path.isabs(executable):
            raise PolicyDeniedError("Native Docker MCP transport is not installed")
        _validate_cli_environment(environment)
        super().__init__(
            params={"command": executable},
            name=name,
            client_session_timeout_seconds=timeout_seconds,
            max_retry_attempts=0,
            require_approval="never",
            failure_error_function=None,
            message_handler=message_handler,
            tool_filter=(
                create_static_tool_filter(allowed_tool_names=list(tool_names))
                if tool_names is not None
                else None
            ),
        )
        self._sessions = session_factory
        self._backends = backends
        self._secrets = secrets
        self._workspace_id = workspace_id
        self._runtime_id = runtime_id
        self._server_id = server_id
        self._command = command
        self._environment = environment
        self._cwd = cwd
        self._reference_id: UUID | None = None

    @asynccontextmanager
    async def create_streams(self) -> AsyncIterator[MCPStreamTransport]:
        # The public SDK stream extension keeps protocol/cancellation native while
        # preventing untrusted MCP stderr from printing credentials into Worker logs.
        # Structured MCP diagnostics remain available through the result metadata.
        with open(os.devnull, "w", encoding="utf-8") as stderr:
            async with stdio_client(self.params, errlog=stderr) as streams:
                yield streams

    async def connect(self) -> None:
        service = SandboxService(self._sessions, self._backends, self._secrets)
        reference = await service.create(
            workspace_id=self._workspace_id,
            runtime_id=self._runtime_id,
            owner_kind="mcp",
            owner_id=uuid4(),
            scope_id=self._server_id,
            manifest=Manifest(root="/workspace"),
        )
        self._reference_id = reference.id
        try:
            native = await service.resume(self._workspace_id, reference.id)
            state = native.state
            if not isinstance(state, DockerSandboxSessionState):
                raise PolicyDeniedError(
                    "Managed stdio requires the installed Docker sandbox backend"
                )
            arguments = ["exec", "-i", "--workdir", self._cwd or str(state.manifest.root)]
            for name in sorted(self._environment):
                arguments.extend(["--env", name])
            arguments.extend([state.container_id, *self._command])
            # Values stay in this local process environment; no secret appears in argv/history.
            environment = {
                key: value for key, value in os.environ.items() if key in _CLI_ENVIRONMENT
            }
            environment.update(self._environment)
            self.params = self.params.model_copy(update={"args": arguments, "env": environment})
            await cast(Callable[[], Awaitable[None]], super().connect)()
        except BaseException:
            await asyncio.shield(self.cleanup())
            raise

    async def cleanup(self) -> None:
        try:
            await cast(Callable[[], Awaitable[None]], super().cleanup)()
        finally:
            try:
                if self._reference_id is not None:
                    service = SandboxService(self._sessions, self._backends, self._secrets)
                    await asyncio.shield(service.delete(self._workspace_id, self._reference_id))
                    self._reference_id = None
            finally:
                # Native deletion can fail. Credentials must still leave the protocol
                # client's parameter object; its durable reference records uncertainty.
                self._environment.clear()
                self.params = self.params.model_copy(update={"env": None, "args": []})


_CLI_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "TMP",
        "TEMP",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
    }
)


def _validate_cli_environment(environment: dict[str, Any]) -> None:
    for key, value in environment.items():
        if (
            not isinstance(value, str)
            or not key
            or not key.replace("_", "a").isalnum()
            or key[0].isdigit()
            or key in _CLI_ENVIRONMENT
            or key.startswith(("DOCKER_", "LD_", "DYLD_", "PYTHON"))
        ):
            raise PolicyDeniedError("MCP credentials contain a reserved process environment name")
