from __future__ import annotations

import base64
import io
import json
import math
import re
import socket
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from subprocess import TimeoutExpired
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import docker
from docker.client import DockerClient
from docker.errors import NotFound
from docker.models.containers import Container
from docker.types import Mount
from requests.exceptions import Timeout as RequestsTimeout
from sqlalchemy.orm import object_session

from opsmesh.runtime.agent_host.channel import DockerAgentChannel
from opsmesh.runtime.backends.contracts import RuntimeBackendCapabilities
from opsmesh.runtime.contracts import (
    SandboxCommandResult,
    SandboxManifest,
    SandboxSession,
    SandboxSessionExecutor,
)
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore, allocation_identity
from opsmesh.runtime.instances.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeProjectFilesystem,
)
from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.instances.project_files import DockerRunProjectFilesystem

_COMMAND_INPUT_LIMIT_BYTES = 1_048_576
_INPUT_ARGUMENT_NAME = re.compile(r"^--[a-z][a-z0-9-]*$")

DockerClientFactory = Callable[[int], DockerClient]


class DockerRuntimeBackend:
    capabilities = RuntimeBackendCapabilities(True, False, True, True)

    def __init__(
        self, client: DockerRuntimeClient | None, transfer_timeout: Callable[[], int]
    ) -> None:
        self._client = client
        self._transfer_timeout = transfer_timeout

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem:
        if self._client is None:
            raise RuntimeError("Docker project files require a worker-injected client")
        return DockerRunProjectFilesystem(
            self._client, runtime, run_id, timeout_seconds=self._transfer_timeout()
        )

    def sandbox_session(
        self,
        manifest: SandboxManifest,
        runtime: WorkspaceRuntime,
    ) -> SandboxSession:
        if not runtime.docker_container_id:
            raise RuntimeError("Docker runtime has no active container")
        if self._client is None:
            raise RuntimeError("Docker sandbox sessions require a worker-injected client")
        return SandboxSession(
            session_id=str(manifest.run_id),
            root=manifest.root,
            backend="docker",
            executor=DockerSandboxSessionExecutor(
                client=self._client,
                container_id=runtime.docker_container_id,
                root=manifest.root,
                timeout_seconds=_runtime_limit(runtime, "timeout_seconds", 300),
                max_file_bytes=_runtime_limit(runtime, "max_output_bytes", 256_000),
                identity=_run_identity(runtime, manifest.run_id),
            ),
            persistent=runtime.execution_mode == "shared",
        )


@dataclass(frozen=True, slots=True)
class DockerSandboxSessionExecutor(SandboxSessionExecutor):
    client: DockerRuntimeClient
    container_id: str
    root: str
    timeout_seconds: int
    max_file_bytes: int
    identity: RuntimeExecutionIdentity

    def execute(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        working_dir: str,
    ) -> SandboxCommandResult:
        bounded_timeout = max(1, min(math.ceil(timeout_seconds), self.timeout_seconds))
        result = self.client.exec_command(
            self.container_id,
            command,
            bounded_timeout,
            working_dir=working_dir,
            identity=self.identity,
        )
        stdout = _bounded_bytes(result.stdout.encode("utf-8"), self.max_file_bytes)
        stderr = _bounded_bytes(result.stderr.encode("utf-8"), self.max_file_bytes)
        return SandboxCommandResult(
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def read_file(self, path: PurePosixPath) -> bytes | None:
        return self.client.copy_file_from_container(
            self.container_id,
            path.as_posix(),
            self.max_file_bytes,
            self.timeout_seconds,
            identity=self.identity,
        )

    def write_file(self, path: PurePosixPath, data: BinaryIO) -> None:
        payload = data.read(self.max_file_bytes + 1)
        if not isinstance(payload, bytes):
            raise TypeError("Sandbox file writes require a binary stream")
        if len(payload) > self.max_file_bytes:
            raise ValueError("Sandbox file exceeds the runtime transfer limit")
        writer = (
            "import os,sys; content=open(sys.argv[-1],'rb').read(); "
            "os.makedirs(os.path.dirname(sys.argv[1]),exist_ok=True); "
            "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600); "
            "\nwith os.fdopen(fd,'wb') as output: output.write(content)"
        )
        result = self.client.exec_command(
            self.container_id,
            ["python", "-c", writer, path.as_posix()],
            self.timeout_seconds,
            input_file=RuntimeCommandInputFile(content=payload, argument_name="--input-file"),
            working_dir=self.root,
            identity=self.identity,
        )
        if result.exit_code:
            raise RuntimeError("Sandbox file write failed")

    def running(self) -> bool:
        return self.client.container_running(self.container_id)


class DockerSdkRuntimeClient(DockerRuntimeClient):
    """Docker runtime boundary implemented exclusively with the official Docker SDK."""

    def __init__(
        self, control_timeout: Callable[[], int], client_factory: DockerClientFactory | None = None
    ) -> None:
        self._control_timeout = control_timeout
        self._client_factory = client_factory or _create_docker_client

    def node_identity(self) -> str:
        return self._node_identity

    @cached_property
    def _node_identity(self) -> str:
        with self._client(self._control_timeout()) as client:
            identity = client.info().get("ID")
        if not isinstance(identity, str) or not identity:
            raise RuntimeError("Docker execution node identity is unavailable")
        return identity

    def open_agent_channel(
        self, container_id: str, *, working_dir: str, identity: RuntimeExecutionIdentity
    ) -> DockerAgentChannel:
        return self._open_channel(
            container_id, "opsmesh.bootstrap.agent_host", working_dir, identity
        )

    def open_mcp_channel(
        self, container_id: str, *, working_dir: str, identity: RuntimeExecutionIdentity
    ) -> DockerAgentChannel:
        return self._open_channel(container_id, "opsmesh_runtime.mcp_host", working_dir, identity)

    def _open_channel(
        self, container_id: str, module: str, working_dir: str, identity: RuntimeExecutionIdentity
    ) -> DockerAgentChannel:
        client = self._client_factory(self._control_timeout())
        try:
            execution = client.api.exec_create(
                container_id,
                [
                    "python",
                    "-m",
                    "opsmesh_runtime.execution_launch",
                    str(identity.allocation_id),
                    str(identity.uid),
                    module,
                ],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=False,
                workdir=working_dir,
                user=f"{identity.uid}:{identity.uid}",
                environment=identity.environment(),
            )
            connection = client.api.exec_start(execution["Id"], socket=True)
            return DockerAgentChannel(connection, client)
        except BaseException:
            client.close()
            raise

    def configure_execution(self, container_id: str, identity: RuntimeExecutionIdentity) -> None:
        self._execution_control(container_id, identity, "configure")

    def revoke_execution(self, container_id: str, identity: RuntimeExecutionIdentity) -> None:
        self._execution_control(container_id, identity, "revoke")

    def _execution_control(
        self, container_id: str, identity: RuntimeExecutionIdentity, action: str
    ) -> None:
        content = json.dumps({"action": action, "execution": identity.payload()}).encode()
        with self._client(self._control_timeout()) as client:
            container = client.containers.get(container_id)
            result = _exec_stdin(
                container,
                ["python", "-m", "opsmesh_runtime.execution_control"],
                content,
                timeout_seconds=self._control_timeout(),
                user="0:0",
            )
            if result != 0:
                raise RuntimeError("Runtime execution identity control failed")

    def terminate_agent_process(
        self, container_id: str, pid: int, *, identity: RuntimeExecutionIdentity
    ) -> None:
        if pid <= 1:
            raise ValueError("SDK process identity is invalid")
        with self._client(self._control_timeout()) as client:
            container = client.containers.get(container_id)
            # TERM allows SDK cleanup, then KILL bounds shutdown for uncooperative
            # project subprocesses. Signals target only this invocation's group.
            script = (
                "import os,signal,sys,time; pid=int(sys.argv[1]); "
                "\nfor sig in (signal.SIGTERM,signal.SIGKILL):"
                "\n try: os.killpg(pid,sig)"
                "\n except ProcessLookupError: break"
                "\n if sig == signal.SIGTERM: time.sleep(0.1)"
            )
            result = _exec(
                container,
                _execution_command(["python", "-c", script, str(pid)], identity),
                working_dir="/",
                user=f"{identity.uid}:{identity.uid}",
            )
            if result.exit_code != 0:
                raise RuntimeError("Runtime SDK process termination failed")

    def create_container(self, request: RuntimeCreateRequest) -> str:
        labels = {
            "opsmesh.workspace_id": request.workspace_id,
            **request.labels,
        }
        if request.runtime_id is not None:
            labels["opsmesh.runtime_id"] = request.runtime_id
        if request.runtime_space_id is not None:
            labels["opsmesh.runtime_space_id"] = request.runtime_space_id

        with self._client(self._control_timeout()) as client:
            if request.process is not None or request.shared_host:
                try:
                    existing = client.containers.get(request.name)
                except NotFound:
                    pass
                else:
                    config = existing.attrs.get("Config", {})
                    if config.get("Image") != request.image or any(
                        existing.labels.get(key) != value for key, value in labels.items()
                    ):
                        raise ValueError("Runtime process identity conflict")
                    return str(existing.id)
            container = client.containers.create(
                image=request.image,
                command=None
                if request.shared_host or request.process is not None
                else ["sleep", "infinity"],
                name=request.name,
                labels=labels,
                nano_cpus=int(request.limits.cpu_count * 1_000_000_000),
                mem_limit=f"{request.limits.memory_mb}m",
                pids_limit=request.limits.max_processes,
                storage_opt={"size": f"{request.limits.disk_mb}m"},
                network_mode=_docker_network_mode(request),
                environment={
                    **(request.process.environment if request.process is not None else {}),
                },
                cap_drop=list(request.hardening.cap_drop),
                cap_add=["NET_ADMIN", "KILL", "CHOWN", "DAC_OVERRIDE", "SETUID", "SETGID"]
                if request.shared_host
                else [],
                security_opt=_security_options(request),
                read_only=request.hardening.read_only_rootfs,
                tmpfs={
                    mount.target: f"{mount.mode},size={mount.size_mb}m"
                    for mount in request.hardening.tmpfs
                },
                mounts=[
                    Mount(
                        target=mount.target,
                        source=mount.source,
                        type=mount.mount_type,
                        read_only=mount.read_only,
                    )
                    for mount in request.mounts
                ],
                user="0:0" if request.shared_host else request.hardening.user,
                init=True,
                working_dir=request.working_dir,
            )
            return str(container.id)

    def start_container(self, container_id: str) -> None:
        with self._client(self._control_timeout()) as client:
            client.containers.get(container_id).start()

    def stop_container(self, container_id: str) -> None:
        with self._client(self._control_timeout()) as client:
            client.containers.get(container_id).stop(timeout=self._control_timeout())

    def remove_container(self, container_id: str) -> None:
        try:
            with self._client(self._control_timeout()) as client:
                client.containers.get(container_id).remove(force=True)
        except NotFound:
            return

    def remove_volume(self, volume_name: str) -> None:
        try:
            with self._client(self._control_timeout()) as client:
                client.volumes.get(volume_name).remove(force=True)
        except NotFound:
            return

    def container_running(self, container_id: str) -> bool:
        try:
            with self._client(self._control_timeout()) as client:
                container = client.containers.get(container_id)
                container.reload()
                return container.status == "running"
        except NotFound:
            return False

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
        identity: RuntimeExecutionIdentity | None = None,
    ) -> RuntimeCommandResult:
        _require_positive_timeout(timeout_seconds)
        if not command:
            raise ValueError("Runtime command must not be empty")
        try:
            with self._client(timeout_seconds) as client:
                container = client.containers.get(container_id)
                if input_file is None:
                    return _exec(
                        container,
                        _execution_command(command, identity),
                        working_dir=working_dir,
                        user=f"{identity.uid}:{identity.uid}" if identity else "0:0",
                        environment=identity.environment() if identity else None,
                    )
                return self._exec_with_input_file(
                    container,
                    command,
                    input_file,
                    timeout_seconds=timeout_seconds,
                    working_dir=working_dir,
                    identity=identity,
                )
        except RequestsTimeout as exc:
            raise TimeoutExpired(command, timeout_seconds) from exc

    def copy_archive_to_container(
        self,
        container_id: str,
        destination_path: str,
        archive: bytes,
        timeout_seconds: int,
    ) -> None:
        _require_positive_timeout(timeout_seconds)
        with self._client(timeout_seconds) as client:
            container = client.containers.get(container_id)
            validated = _rewrite_archive_owner(archive, uid=0, gid=0)
            extractor = (
                "import io,sys,tarfile; data=sys.stdin.buffer.read(); "
                "archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:'); "
                "archive.extractall(sys.argv[1],filter='data')"
            )
            exit_code = _exec_stdin(
                container,
                ["python", "-c", extractor, destination_path],
                validated,
                timeout_seconds=timeout_seconds,
                user="0:0",
            )
        if exit_code:
            raise RuntimeError("Runtime input archive staging failed")

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
        *,
        identity: RuntimeExecutionIdentity,
    ) -> bytes | None:
        if max_bytes < 0:
            raise ValueError("Runtime file read limit must not be negative")
        _require_positive_timeout(timeout_seconds)
        reader = (
            "import os,sys,stat,json,base64; "
            "\ntry: fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW)"
            "\nexcept FileNotFoundError: print('null'); sys.exit(0)"
            "\nwith os.fdopen(fd,'rb') as source:"
            "\n assert stat.S_ISREG(os.fstat(source.fileno()).st_mode)"
            "\n content=source.read(int(sys.argv[2])+1)"
            "\n assert len(content)<=int(sys.argv[2])"
            "\nprint(json.dumps(base64.b64encode(content).decode()))"
        )
        result = self.exec_command(
            container_id,
            ["python", "-c", reader, source_path, str(max_bytes)],
            timeout_seconds,
            working_dir="/",
            identity=identity,
        )
        if result.exit_code:
            raise ValueError("Runtime file read failed or exceeds its transfer limit")
        payload = json.loads(result.stdout)
        return None if payload is None else base64.b64decode(payload, validate=True)

    def _exec_with_input_file(
        self,
        container: Container,
        command: list[str],
        input_file: RuntimeCommandInputFile,
        *,
        timeout_seconds: int,
        working_dir: str | None,
        identity: RuntimeExecutionIdentity | None,
    ) -> RuntimeCommandResult:
        if not _INPUT_ARGUMENT_NAME.fullmatch(input_file.argument_name):
            raise ValueError("Runtime input argument name is invalid")
        if len(input_file.content) > _COMMAND_INPUT_LIMIT_BYTES:
            raise ValueError("Runtime command input exceeds its transfer limit")
        directory_name = f"opsmesh-command-input-{uuid4()}"
        parent = f"/tmp/opsmesh-runs/{identity.uid}" if identity else "/tmp"
        container_directory = f"{parent}/{directory_name}"
        container_path = f"{container_directory}/request.json"
        try:
            _write_input_file(
                container,
                container_directory,
                container_path,
                input_file.content,
                timeout_seconds=timeout_seconds,
                user=f"{identity.uid}:{identity.uid}" if identity else "0:0",
                identity=identity,
            )
            return _exec(
                container,
                _execution_command([*command, input_file.argument_name, container_path], identity),
                working_dir=working_dir,
                user=f"{identity.uid}:{identity.uid}" if identity else "0:0",
                environment=identity.environment() if identity else None,
            )
        finally:
            _remove_input_file(container, container_path, container_directory)

    @contextmanager
    def _client(self, timeout_seconds: int) -> Iterator[DockerClient]:
        client = self._client_factory(timeout_seconds)
        try:
            yield client
        finally:
            client.close()


def _create_docker_client(timeout_seconds: int) -> DockerClient:
    return docker.from_env(timeout=timeout_seconds)


def _docker_network_mode(request: RuntimeCreateRequest) -> str:
    if request.shared_host:
        return "bridge"
    policy = request.network_policy
    mode = policy.get("mode")
    if request.network_disabled or mode in {None, "none"}:
        return "none"
    if mode == "internet":
        return "bridge"
    if mode == "restricted":
        raise ValueError("Restricted egress requires an execution supervisor")
    raise ValueError("Runtime egress mode is unsupported")


def _security_options(request: RuntimeCreateRequest) -> list[str]:
    options = list(request.hardening.security_opt)
    if request.hardening.apparmor_profile:
        options.append(f"apparmor={request.hardening.apparmor_profile}")
    if request.hardening.seccomp_profile not in {"", "default"}:
        options.append(f"seccomp={request.hardening.seccomp_profile}")
    return options


def _exec(
    container: Container,
    command: list[str],
    *,
    working_dir: str | None,
    user: str = "0:0",
    environment: dict[str, str] | None = None,
) -> RuntimeCommandResult:
    result = container.exec_run(
        command, workdir=working_dir, demux=True, user=user, environment=environment
    )
    if not isinstance(result.output, tuple) or len(result.output) != 2:
        raise RuntimeError("Docker returned an invalid demultiplexed command response")
    if result.exit_code is None:
        raise RuntimeError("Docker command did not return an exit code")
    stdout, stderr = result.output
    return RuntimeCommandResult(
        exit_code=result.exit_code,
        stdout=_decode_output(stdout),
        stderr=_decode_output(stderr),
    )


def _write_input_file(
    container: Container,
    directory: str,
    path: str,
    content: bytes,
    *,
    timeout_seconds: int,
    user: str,
    identity: RuntimeExecutionIdentity | None = None,
) -> None:
    # Docker's archive endpoint rejects read-only rootfs, including writable tmpfs mounts.
    # Exec stdin keeps credentials out of argv and writes as the runtime's own user.
    writer = (
        "import os,sys; os.mkdir(sys.argv[1],0o700); "
        "fd=os.open(sys.argv[2],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400); "
        "data=sys.stdin.buffer.read(int(sys.argv[3])+1); "
        "assert len(data)<=int(sys.argv[3]); "
        "f=os.fdopen(fd,'wb'); f.write(data); f.close()"
    )
    exit_code = _exec_stdin(
        container,
        _execution_command(["python", "-c", writer, directory, path, str(len(content))], identity),
        content,
        timeout_seconds=timeout_seconds,
        user=user,
    )
    if exit_code != 0:
        raise RuntimeError("Runtime command input transfer failed")


def _execution_command(command: list[str], identity: RuntimeExecutionIdentity | None) -> list[str]:
    if identity is None:
        return command
    return [
        "python",
        "-m",
        "opsmesh_runtime.execution_launch",
        str(identity.allocation_id),
        str(identity.uid),
        "--command",
        *command,
    ]


def _exec_stdin(
    container: Container, command: list[str], content: bytes, *, timeout_seconds: int, user: str
) -> int:
    if container.client is None:
        raise RuntimeError("Runtime container has no Docker client")
    api = container.client.api
    execution = api.exec_create(
        container.id,
        command,
        stdin=True,
        stdout=True,
        stderr=True,
        user=user,
    )
    connection = api.exec_start(execution["Id"], socket=True)
    try:
        transport: Any = getattr(connection, "_sock", connection)
        transport.settimeout(timeout_seconds)
        transport.sendall(content)
        transport.shutdown(socket.SHUT_WR)
        while transport.recv(4096):
            pass
    finally:
        try:
            response = getattr(connection, "_response", None)
            if response is not None:
                response.close()
        finally:
            connection.close()
    exit_code = api.exec_inspect(execution["Id"])["ExitCode"]
    if not isinstance(exit_code, int):
        raise RuntimeError("Runtime control process did not exit")
    return exit_code


def _runtime_limit(runtime: WorkspaceRuntime, key: str, default: int) -> int:
    value = runtime.limits.get(key)
    return value if isinstance(value, int) and value > 0 else default


def _run_identity(runtime: WorkspaceRuntime, run_id: UUID) -> RuntimeExecutionIdentity:
    session = object_session(runtime)
    if session is None:
        raise RuntimeError("Runtime identity requires a persisted allocation")
    allocation = RuntimeAllocationStore(session).get(runtime, "run", run_id)
    if allocation is None:
        raise RuntimeError("Run has no Runtime execution identity")
    return allocation_identity(allocation)


def _bounded_bytes(content: bytes, limit: int) -> bytes:
    if len(content) <= limit:
        return content
    marker = b"\n[output truncated by OpsMesh runtime policy]\n"
    retained = max(0, limit - len(marker))
    return content[:retained] + marker[: limit - retained]


def _rewrite_archive_owner(archive: bytes, *, uid: int, gid: int) -> bytes:
    output = io.BytesIO()
    try:
        with (
            tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source,
            tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as target,
        ):
            for member in source.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError("Runtime input archive contains an unsupported entry")
                normalized_parts = member.name.split("/")
                if (
                    not member.name
                    or "\\" in member.name
                    or member.name.startswith("/")
                    or any(part in {"", ".", ".."} for part in normalized_parts)
                ):
                    raise ValueError("Runtime input archive contains an unsafe path")
                member.uid = uid
                member.gid = gid
                member.uname = ""
                member.gname = ""
                stream = source.extractfile(member) if member.isfile() else None
                target.addfile(member, stream)
    except tarfile.TarError as exc:
        raise ValueError("Runtime input archive is invalid") from exc
    return output.getvalue()


def _remove_input_file(container: Container, path: str, directory: str) -> None:
    removed_file = _exec(container, ["rm", "-f", path], working_dir="/")
    removed_directory = _exec(container, ["rmdir", directory], working_dir="/")
    if removed_file.exit_code != 0 or removed_directory.exit_code != 0:
        raise RuntimeError("Runtime command input cleanup failed")


def _decode_output(output: bytes | None) -> str:
    if output is None:
        return ""
    return output.decode("utf-8", errors="replace")


def _require_positive_timeout(timeout_seconds: int) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Docker operation timeout must be positive")
