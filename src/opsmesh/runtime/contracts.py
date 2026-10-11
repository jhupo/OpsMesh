from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import BinaryIO, Literal, Protocol, cast
from uuid import UUID


class SandboxMode(StrEnum):
    ISOLATED = "isolated"
    SHARED = "shared"


class SandboxCapability(StrEnum):
    SHELL = "shell"
    STDIO_MCP = "stdio_mcp"
    PROJECT_FILES = "project_files"


RuntimeExecutionMode = Literal["isolated", "shared"]


def validate_runtime_execution_mode(
    execution_mode: str,
) -> RuntimeExecutionMode:
    if execution_mode not in {"isolated", "shared"}:
        raise ValueError("Runtime execution mode is unsupported")
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
class SandboxCommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class SandboxSessionExecutor(Protocol):
    """Operations available inside one already-authorized runtime session."""

    def execute(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        working_dir: str,
    ) -> SandboxCommandResult: ...

    def read_file(self, path: PurePosixPath) -> bytes | None: ...

    def write_file(self, path: PurePosixPath, data: BinaryIO) -> None: ...

    def running(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class SandboxSession:
    session_id: str
    root: str
    backend: str
    executor: SandboxSessionExecutor
    persistent: bool = False


@dataclass(frozen=True, slots=True)
class SandboxBinding:
    manifest: SandboxManifest
    session: SandboxSession


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    mode: SandboxMode
    allow_shell: bool
    allow_stdio_mcp: bool
    allow_project_files: bool

    @classmethod
    def for_mode(cls, mode: SandboxMode) -> SandboxPolicy:
        return cls(mode, True, True, True)

    def require(self, capability: str) -> None:
        allowed = {
            SandboxCapability.SHELL.value: self.allow_shell,
            SandboxCapability.STDIO_MCP.value: self.allow_stdio_mcp,
            SandboxCapability.PROJECT_FILES.value: self.allow_project_files,
        }.get(capability)
        if allowed is not True:
            raise PermissionError(f"{capability} requires an execution sandbox")
