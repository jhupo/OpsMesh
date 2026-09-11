from __future__ import annotations

from dataclasses import dataclass

from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeCommandResult


@dataclass(frozen=True, slots=True)
class RuntimeSdkProcess:
    """Runs a provider SDK CLI inside an already-authorized runtime container."""

    client: DockerRuntimeClient
    container_id: str
    working_dir: str

    def run(self, command: list[str], timeout_seconds: int = 900) -> RuntimeCommandResult:
        if not command or any(not item.strip() for item in command):
            raise ValueError("SDK process command must not be empty")
        return self.client.exec_command(
            self.container_id,
            command,
            timeout_seconds,
            working_dir=self.working_dir,
        )
