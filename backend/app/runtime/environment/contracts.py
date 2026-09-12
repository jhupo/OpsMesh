from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

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
    network_policy: dict[str, object] = field(default_factory=dict)
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
    mount_type: Literal["bind", "volume", "tmpfs", "npipe"] = "volume"
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
    user: str | None = "65532:65532"
    user_policy: str = "fixed_non_root"
    user_enforced: bool = True
    seccomp_profile: str = "default"
    apparmor_profile: str = "docker-default"


@dataclass(frozen=True)
class RuntimeCommandResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RuntimeCommandInputFile:
    content: bytes
    argument_name: str


class RuntimeProjectFilesystem(Protocol):
    @property
    def root_path(self) -> str: ...

    def stage_archive(self, archive: bytes) -> None: ...

    def read_file(self, relative_path: str, *, max_bytes: int) -> bytes | None: ...

    def cleanup(self) -> None: ...


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
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult: ...

    def copy_archive_to_container(
        self,
        container_id: str,
        destination_path: str,
        archive: bytes,
        timeout_seconds: int,
    ) -> None: ...

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes | None: ...
