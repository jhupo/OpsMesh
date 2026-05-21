import subprocess

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)


class DockerCliRuntimeClient(DockerRuntimeClient):
    def create_container(self, request: RuntimeCreateRequest) -> str:
        command = [
            "docker",
            "create",
            "--label",
            f"chaincloud.workspace_id={request.workspace_id}",
            "--name",
            request.name,
            "--cpus",
            str(request.limits.cpu_count),
            "--memory",
            f"{request.limits.memory_mb}m",
            "--pids-limit",
            str(request.limits.max_processes),
            "--storage-opt",
            f"size={request.limits.disk_mb}m",
            "--network",
            "none" if request.network_disabled else "bridge",
            request.image,
            "sleep",
            "infinity",
        ]
        return self._run(command, timeout_seconds=30).stdout.strip()

    def start_container(self, container_id: str) -> None:
        self._run(["docker", "start", container_id], timeout_seconds=30)

    def stop_container(self, container_id: str) -> None:
        self._run(["docker", "stop", container_id], timeout_seconds=30)

    def remove_container(self, container_id: str) -> None:
        self._run(["docker", "rm", "-f", container_id], timeout_seconds=30)

    def remove_volume(self, volume_name: str) -> None:
        self._run(["docker", "volume", "rm", "-f", volume_name], timeout_seconds=30)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        result = self._run(
            ["docker", "exec", container_id, *command],
            timeout_seconds=timeout_seconds,
            check=False,
        )
        return RuntimeCommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        check: bool = True,
    ) -> RuntimeCommandResult:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        if check and completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout)
        return RuntimeCommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
