"""SDK-neutral execution environments; resource requests must be enforced."""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ExecutionSpec(OpsMeshContract):
    workspace: Path
    provider: Literal["local", "docker"] = "local"
    trusted_local: bool = False
    image: str | None = None
    cpu: float | None = Field(default=None, gt=0)
    memory_mb: int | None = Field(default=None, gt=0)
    pids_limit: int | None = Field(default=None, ge=8, le=65536)
    disk_mb: int | None = Field(default=None, gt=0)
    network: Literal["default", "none"] = "default"
    environment: dict[str, str] = Field(default_factory=dict)


class CommandRequest(OpsMeshContract):
    id: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = Field(default=60, gt=0)
    max_output_bytes: int = Field(default=1_048_576, gt=0)


class CommandResult(OpsMeshContract):
    id: str
    exit_code: int
    stdout: str
    stderr: str
    output_truncated: bool = False


class ExecutionSession(Protocol):
    id: str

    async def execute(self, request: CommandRequest) -> CommandResult: ...
    async def cancel(self, command_id: str) -> None: ...
    async def close(self) -> None: ...


class ExecutionProvider(Protocol):
    async def create(self, spec: ExecutionSpec) -> ExecutionSession: ...
