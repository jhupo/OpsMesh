from __future__ import annotations

from dataclasses import dataclass

from backend.app.agent_runtime.sandbox.contracts import SandboxMode


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
            "shell": self.allow_shell,
            "stdio_mcp": self.allow_stdio_mcp,
            "project_files": self.allow_project_files,
        }.get(capability)
        if allowed is not True:
            raise PermissionError(f"{capability} requires an execution sandbox")
