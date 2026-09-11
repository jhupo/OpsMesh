from __future__ import annotations

import io
import tarfile
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

    def install_claude_cli_wrapper(self) -> str:
        """Install a deterministic Claude CLI entrypoint inside the runtime container."""
        path = "/opt/opsmesh/bin/claude"
        script = b"#!/bin/sh\nexec claude \"$@\"\n"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("claude")
            info.mode = 0o755
            info.size = len(script)
            tar.addfile(info, io.BytesIO(script))
        self.client.copy_archive_to_container(
            self.container_id,
            "/opt/opsmesh/bin",
            archive.getvalue(),
            30,
        )
        return path
