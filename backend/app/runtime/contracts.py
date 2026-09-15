from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, cast
from uuid import UUID


class SandboxMode(StrEnum):
    NONE = "none"
    ISOLATED = "isolated"
    POOLED = "pooled"
    PERSISTENT = "persistent"


class SandboxCapability(StrEnum):
    SHELL = "shell"
    STDIO_MCP = "stdio_mcp"
    PROJECT_FILES = "project_files"


RuntimeExecutionMode = Literal["none", "isolated", "pooled", "persistent"]


def validate_runtime_execution_mode(
    execution_mode: str,
    pool_key: str | None,
) -> RuntimeExecutionMode:
    if execution_mode not in {"none", "isolated", "pooled", "persistent"}:
        raise ValueError("Runtime execution mode is unsupported")
    if execution_mode == "none":
        raise ValueError("Runtime resources cannot use the none execution mode")
    if pool_key is not None and execution_mode != "pooled":
        raise ValueError("Runtime pool key is only valid for pooled execution")
    if pool_key is not None and (not pool_key.strip() or len(pool_key) > 160):
        raise ValueError("Runtime pool key must contain between 1 and 160 characters")
    return cast(RuntimeExecutionMode, execution_mode)


class RuntimeEnvironmentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SandboxManifest:
    """Workspace-scoped execution declaration passed to a runtime backend."""

    run_id: UUID
    root: str
    files: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SandboxSession:
    session_id: str
    root: str
    backend: str
    persistent: bool = False


class SandboxBackend(Protocol):
    """Provider-neutral isolated execution boundary."""

    name: str

    def acquire(self, manifest: SandboxManifest) -> SandboxSession: ...

    def release(self, session: SandboxSession) -> None: ...


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    mode: SandboxMode
    allow_shell: bool
    allow_stdio_mcp: bool
    allow_project_files: bool

    @classmethod
    def for_mode(cls, mode: SandboxMode) -> SandboxPolicy:
        enabled = mode is not SandboxMode.NONE
        return cls(mode, enabled, enabled, enabled)

    def require(self, capability: str) -> None:
        allowed = {
            SandboxCapability.SHELL.value: self.allow_shell,
            SandboxCapability.STDIO_MCP.value: self.allow_stdio_mcp,
            SandboxCapability.PROJECT_FILES.value: self.allow_project_files,
        }.get(capability)
        if allowed is not True:
            raise PermissionError(f"{capability} requires an execution sandbox")
