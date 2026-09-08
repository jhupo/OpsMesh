from __future__ import annotations

import io
import re
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from subprocess import TimeoutExpired
from typing import Any
from uuid import uuid4

import docker
from docker.client import DockerClient
from docker.errors import NotFound
from docker.models.containers import Container
from docker.types import Mount
from requests.exceptions import Timeout as RequestsTimeout

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)

_DOCKER_CONTROL_TIMEOUT_SECONDS = 30
_ARCHIVE_OVERHEAD_LIMIT_BYTES = 1_048_576
_COMMAND_INPUT_LIMIT_BYTES = 1_048_576
_INPUT_ARGUMENT_NAME = re.compile(r"^--[a-z][a-z0-9-]*$")

DockerClientFactory = Callable[[int], DockerClient]


class DockerSdkRuntimeClient(DockerRuntimeClient):
    """Docker runtime boundary implemented exclusively with the official Docker SDK."""

    def __init__(self, client_factory: DockerClientFactory | None = None) -> None:
        self._client_factory = client_factory or _create_docker_client

    def create_container(self, request: RuntimeCreateRequest) -> str:
        labels = {
            "opsmesh.workspace_id": request.workspace_id,
            **request.labels,
        }
        if request.runtime_id is not None:
            labels["opsmesh.runtime_id"] = request.runtime_id
        if request.runtime_space_id is not None:
            labels["opsmesh.runtime_space_id"] = request.runtime_space_id

        with self._client(_DOCKER_CONTROL_TIMEOUT_SECONDS) as client:
            container = client.containers.create(
                image=request.image,
                command=["sleep", "infinity"],
                name=request.name,
                labels=labels,
                nano_cpus=int(request.limits.cpu_count * 1_000_000_000),
                mem_limit=f"{request.limits.memory_mb}m",
                pids_limit=request.limits.max_processes,
                storage_opt={"size": f"{request.limits.disk_mb}m"},
                network_mode="none" if request.network_disabled else "bridge",
                cap_drop=list(request.hardening.cap_drop),
                security_opt=list(request.hardening.security_opt),
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
                user=request.hardening.user,
                working_dir=request.working_dir,
            )
            return str(container.id)

    def start_container(self, container_id: str) -> None:
        with self._client(_DOCKER_CONTROL_TIMEOUT_SECONDS) as client:
            client.containers.get(container_id).start()

    def stop_container(self, container_id: str) -> None:
        with self._client(_DOCKER_CONTROL_TIMEOUT_SECONDS) as client:
            client.containers.get(container_id).stop(timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS)

    def remove_container(self, container_id: str) -> None:
        with self._client(_DOCKER_CONTROL_TIMEOUT_SECONDS) as client:
            client.containers.get(container_id).remove(force=True)

    def remove_volume(self, volume_name: str) -> None:
        with self._client(_DOCKER_CONTROL_TIMEOUT_SECONDS) as client:
            client.volumes.get(volume_name).remove(force=True)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult:
        _require_positive_timeout(timeout_seconds)
        if not command:
            raise ValueError("Runtime command must not be empty")
        try:
            with self._client(timeout_seconds) as client:
                container = client.containers.get(container_id)
                if input_file is None:
                    return _exec(container, command, working_dir=working_dir)
                return self._exec_with_input_file(
                    container,
                    command,
                    input_file,
                    working_dir=working_dir,
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
            uid, gid = _container_identity(container)
            copied = container.put_archive(
                destination_path,
                _rewrite_archive_owner(archive, uid=uid, gid=gid),
            )
        if copied is not True:
            raise RuntimeError("Docker rejected the runtime archive")

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes | None:
        if max_bytes < 0:
            raise ValueError("Runtime file read limit must not be negative")
        _require_positive_timeout(timeout_seconds)
        with self._client(timeout_seconds) as client:
            container = client.containers.get(container_id)
            try:
                stream, _ = container.get_archive(source_path)
            except NotFound:
                return None
            archive = _read_bounded_archive(stream, payload_limit=max_bytes)
        return _single_regular_file_from_tar(archive, max_bytes=max_bytes)

    def _exec_with_input_file(
        self,
        container: Container,
        command: list[str],
        input_file: RuntimeCommandInputFile,
        *,
        working_dir: str | None,
    ) -> RuntimeCommandResult:
        if not _INPUT_ARGUMENT_NAME.fullmatch(input_file.argument_name):
            raise ValueError("Runtime input argument name is invalid")
        if len(input_file.content) > _COMMAND_INPUT_LIMIT_BYTES:
            raise ValueError("Runtime command input exceeds its transfer limit")
        uid, gid = _container_identity(container)
        directory_name = f"opsmesh-command-input-{uuid4()}"
        container_directory = f"/tmp/{directory_name}"
        container_path = f"{container_directory}/request.json"
        archive = _input_file_archive(
            directory_name,
            input_file.content,
            uid=uid,
            gid=gid,
        )
        if container.put_archive("/tmp", archive) is not True:
            raise RuntimeError("Docker rejected the runtime command input")
        try:
            return _exec(
                container,
                [*command, input_file.argument_name, container_path],
                working_dir=working_dir,
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


def _exec(
    container: Container,
    command: list[str],
    *,
    working_dir: str | None,
) -> RuntimeCommandResult:
    result = container.exec_run(command, workdir=working_dir, demux=True)
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


def _container_identity(container: Container) -> tuple[int, int]:
    uid_result = _exec(container, ["id", "-u"], working_dir="/")
    gid_result = _exec(container, ["id", "-g"], working_dir="/")
    if uid_result.exit_code != 0 or gid_result.exit_code != 0:
        raise RuntimeError("Runtime container identity cannot be resolved")
    try:
        uid = int(uid_result.stdout.strip())
        gid = int(gid_result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Runtime container returned an invalid identity") from exc
    if uid < 0 or gid < 0:
        raise RuntimeError("Runtime container returned an invalid identity")
    return uid, gid


def _input_file_archive(directory_name: str, content: bytes, *, uid: int, gid: int) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        directory = tarfile.TarInfo(directory_name)
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o700
        directory.uid = uid
        directory.gid = gid
        tar.addfile(directory)

        file_info = tarfile.TarInfo(f"{directory_name}/request.json")
        file_info.size = len(content)
        file_info.mode = 0o400
        file_info.uid = uid
        file_info.gid = gid
        tar.addfile(file_info, io.BytesIO(content))
    return output.getvalue()


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
                if member.name.startswith("/") or ".." in member.name.split("/"):
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


def _read_bounded_archive(stream: Any, *, payload_limit: int) -> bytes:
    archive_limit = payload_limit + _ARCHIVE_OVERHEAD_LIMIT_BYTES
    chunks: list[bytes] = []
    received = 0
    try:
        for chunk in stream:
            received += len(chunk)
            if received > archive_limit:
                raise ValueError("Runtime output archive exceeds its transfer limit")
            chunks.append(chunk)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return b"".join(chunks)


def _decode_output(output: bytes | None) -> str:
    if output is None:
        return ""
    return output.decode("utf-8", errors="replace")


def _require_positive_timeout(timeout_seconds: int) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Docker operation timeout must be positive")


def _single_regular_file_from_tar(archive: bytes, *, max_bytes: int) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = tar.getmembers()
            if any(member.issym() or member.islnk() for member in members):
                raise ValueError("Runtime output cannot be a symbolic or hard link")
            regular_files = [member for member in members if member.isfile()]
            if len(regular_files) != 1:
                raise ValueError("Runtime output must be exactly one regular file")
            member = regular_files[0]
            if member.size > max_bytes:
                raise ValueError("Runtime file exceeds its transfer limit")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("Runtime output archive is invalid")
            content = stream.read(max_bytes + 1)
    except tarfile.TarError as exc:
        raise ValueError("Runtime output archive is invalid") from exc
    if len(content) > max_bytes or len(content) != member.size:
        raise ValueError("Runtime output size is invalid")
    return content
