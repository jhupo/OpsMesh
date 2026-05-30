from dataclasses import dataclass, field
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
    runtime_id: str | None = None
    runtime_space_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    mounts: tuple["RuntimeMount", ...] = ()
    working_dir: str | None = None


@dataclass(frozen=True)
class RuntimeMount:
    source: str
    target: str
    mount_type: str = "volume"
    read_only: bool = False


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
