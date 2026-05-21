from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RuntimeLimits:
    cpu_count: float
    memory_mb: int
    disk_mb: int
    timeout_seconds: int
    max_output_bytes: int = 256_000
    max_processes: int = 256


@dataclass(frozen=True)
class RuntimeCreateRequest:
    image: str
    name: str
    workspace_id: str
    limits: RuntimeLimits
    network_disabled: bool = True


@dataclass(frozen=True)
class RuntimeCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class DockerRuntimeClient(Protocol):
    def create_container(self, request: RuntimeCreateRequest) -> str: ...

    def start_container(self, container_id: str) -> None: ...

    def stop_container(self, container_id: str) -> None: ...

    def remove_container(self, container_id: str) -> None: ...

    def remove_volume(self, volume_name: str) -> None: ...

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult: ...
