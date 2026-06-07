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
    hardening: "RuntimeHardeningPolicy" = field(default_factory=lambda: RuntimeHardeningPolicy())
    working_dir: str | None = None


@dataclass(frozen=True)
class RuntimeMount:
    source: str
    target: str
    mount_type: str = "volume"
    read_only: bool = False


@dataclass(frozen=True)
class RuntimeTmpfsMount:
    target: str
    size_mb: int
    mode: str = "rw,noexec,nosuid,nodev"


@dataclass(frozen=True)
class RuntimeHardeningPolicy:
    cap_drop: tuple[str, ...] = ("ALL",)
    security_opt: tuple[str, ...] = ("no-new-privileges:true",)
    read_only_rootfs: bool = True
    tmpfs: tuple[RuntimeTmpfsMount, ...] = (
        RuntimeTmpfsMount(target="/tmp", size_mb=64),
        RuntimeTmpfsMount(target="/var/tmp", size_mb=16),
    )
    user: str | None = None
    user_policy: str = "image_default"
    user_enforced: bool = False


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
