from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
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


@dataclass(frozen=True, slots=True)
class SandboxManifest:
    """Product-owned workspace declaration passed to a provider SDK."""

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
    """Execution boundary shared by all provider adapters."""

    name: str

    def acquire(self, manifest: SandboxManifest) -> SandboxSession: ...

    def release(self, session: SandboxSession) -> None: ...


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Run capability policy shared by provider adapters."""

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
