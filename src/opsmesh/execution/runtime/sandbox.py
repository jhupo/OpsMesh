"""Native Docker lifecycle, borrowed SDK clients, private ACLs and bounded file transfer."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import stat
from collections.abc import Callable
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import UUID, uuid4

import docker
from agents.sandbox import ExecResult, Manifest, User
from agents.sandbox.entries import Mount
from agents.sandbox.errors import WorkspaceArchiveReadError, WorkspaceReadNotFoundError
from agents.sandbox.sandboxes.docker import (
    DockerSandboxClient,
    DockerSandboxClientOptions,
    DockerSandboxSession,
    DockerSandboxSessionState,
)
from agents.sandbox.session import (
    BaseSandboxClient,
    BaseSandboxSession,
    SandboxSession,
    SandboxSessionState,
)
from agents.sandbox.snapshot import NoopSnapshot, SnapshotBase, SnapshotSpec
from pydantic import Field

from opsmesh.execution.runs.access import authorize_run
from opsmesh.execution.runs.models import ExecutionOperation
from opsmesh.execution.runtime.directory_access import grant_directory_access
from opsmesh.execution.runtime.helpers.source import helper_source
from opsmesh.execution.runtime.manifest import trusted_manifest, workspace_manifest
from opsmesh.execution.runtime.models import SandboxEnvironment
from opsmesh.execution.runtime.schemas import (
    MAX_FILE_BYTES,
    SANDBOX_WORKDIR,
    SandboxCommandCancelled,
    SandboxDirectoryAuthority,
)
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.files import validate_relative_path
from opsmesh.shared.serialization import payload_digest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from opsmesh.shared.security.encryption import SecretCipher


class SandboxOwner(Protocol):
    sessions: sessionmaker[Session]
    cipher: SecretCipher


async def read_file(session: SandboxSession, path: str) -> str:
    relative = validate_relative_path(path)
    result = await session.exec(
        "python3",
        "-I",
        "-c",
        helper_source("read_file"),
        SANDBOX_WORKDIR,
        relative,
        str(MAX_FILE_BYTES),
        shell=False,
        timeout=60,
    )
    if result.exit_code == 66:
        raise WorkspaceReadNotFoundError(path=Path(relative))
    if result.exit_code != 0:
        raise WorkspaceArchiveReadError(
            path=Path(relative), context={"exit_code": result.exit_code}, retryable=False
        )
    if len(result.stdout) > MAX_FILE_BYTES:
        raise ValueError("Sandbox file exceeds the transfer limit")
    return base64.b64encode(result.stdout).decode("ascii")


async def write_file(session: SandboxSession, path: str, encoded: str) -> int:
    data = base64.b64decode(encoded, validate=True)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Sandbox file exceeds the transfer limit")
    relative = validate_relative_path(path)
    # SDK write() performs a path check then cp, which can race a sandbox
    # symlink replacement. Stage at a server-generated location and commit with
    # descriptor-relative traversal/rename inside the mounted directory.
    staged = Path("/tmp/opsmesh_runtime") / f"transfer-{uuid4().hex}"
    with BytesIO(data) as stream:
        await session.write(staged, stream)
    result = await session.exec(
        "python3",
        "-I",
        "-c",
        helper_source("write_file"),
        SANDBOX_WORKDIR,
        relative,
        str(staged),
        str(MAX_FILE_BYTES),
        shell=False,
        timeout=60,
    )
    if result.exit_code != 0:
        raise ValueError("Sandbox file could not be written safely")
    return len(data)


class SandboxBackend:
    def __init__(self, directories: SandboxDirectoryAuthority | None = None) -> None:
        self.directories = directories
        self._client: DockerSandboxClient | None = None

    @property
    def client(self) -> DockerSandboxClient:
        if self._client is None:
            self._client = DockerSandboxClient(docker.from_env())
        return self._client

    def directory(self, labels: dict[str, str]) -> tuple[Path, dict[str, str]]:
        if self.directories is None:
            raise ConflictError("The sandbox directory authority is not assembled")
        try:
            directory = self.directories(
                environment_id=UUID(labels["opsmesh.environment"]),
                principal_id=UUID(labels["opsmesh.principal"]),
                directory_id=UUID(labels["opsmesh.directory"])
                if labels.get("opsmesh.directory")
                else None,
            )
        except (KeyError, ValueError):
            raise ConflictError("The sandbox has no authoritative directory identity") from None
        path = directory.host_path.resolve(strict=True)
        if not path.is_dir():
            raise ConflictError("The sandbox directory is unavailable")
        return path, {**labels, "opsmesh.directory": str(directory.directory_id)}

    async def create(self, *, image: str, labels: dict[str, str]) -> SandboxSession:
        path, labels = await asyncio.to_thread(self.directory, labels)
        return await self.client.create(
            manifest=trusted_manifest(path),
            options=DockerSandboxClientOptions(image=image, labels=labels),
        )

    async def prepare_directory(self, native: SandboxSession) -> None:
        if not isinstance(native.state, DockerSandboxSessionState):
            raise ConflictError("The environment has an unsupported native backend")
        result = await native.exec("id", "-u", shell=False, timeout=10)
        if result.exit_code != 0 or not result.stdout.strip().isdigit():
            raise ConflictError("The sandbox execution identity is unavailable")
        path, _ = await asyncio.to_thread(self.directory, native.state.labels or {})
        container = await asyncio.to_thread(self.client.get_container, native.state.container_id)
        if container is None:
            raise ConflictError("The native environment is missing")
        await asyncio.to_thread(validate_container, container, native.state, path)
        await grant_directory_access(native, int(result.stdout.strip()), os.getegid())

    async def initialize_directory(self, native: SandboxSession) -> None:
        if not isinstance(native.state, DockerSandboxSessionState):
            raise ConflictError("The environment has an unsupported native backend")
        path, _ = await asyncio.to_thread(self.directory, native.state.labels or {})
        manifest = workspace_manifest()
        # Materialize into the granted persistent directory, keeping the Docker
        # backend's private helper root and authoritative mount state unchanged.
        for relative, entry in manifest.validated_entries().items():
            try:
                existing = await asyncio.to_thread((path / relative).lstat)
            except FileNotFoundError:
                await entry.apply(native, Path(manifest.root) / relative, Path("/"))
            else:
                if not stat.S_ISDIR(existing.st_mode):
                    raise ConflictError("A workspace layout directory is occupied by another entry")

    async def resume(self, payload: dict[str, object]) -> SandboxSession:
        authority = payload.get("opsmesh_authority")
        if not isinstance(authority, dict):
            raise ConflictError("The sandbox has no persisted mount authority")
        labels = authority.get("labels")
        if not isinstance(labels, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
        ):
            raise ConflictError("The sandbox authority is invalid")
        path, current_labels = await asyncio.to_thread(self.directory, labels)
        if current_labels != labels or authority.get("host_digest") != path_digest(path):
            raise ConflictError("The sandbox directory authority changed")
        native_payload = {
            key: value for key, value in payload.items() if key != "opsmesh_authority"
        }
        state = self.client.deserialize_session_state(native_payload)
        if not isinstance(state, DockerSandboxSessionState) or state.labels != labels:
            raise ConflictError("The environment has an unsupported native identity")
        rebound = state.rebind_persisted_path_grants(trusted_manifest(path))
        if not isinstance(rebound, DockerSandboxSessionState):
            raise ConflictError("The environment has an unsupported native backend")
        rebound.assert_path_grants_rebound()
        container = await asyncio.to_thread(self.client.get_container, rebound.container_id)
        if container is None:
            raise ConflictError("The native environment is missing; reconciliation is required")
        await asyncio.to_thread(validate_container, container, rebound, path)
        # The public SDK resume convenience method deliberately recreates sessions
        # carrying host grants. The public constructor attaches to the verified
        # existing Docker resource without changing its identity or lifecycle.
        return SandboxSession(
            DockerSandboxSession(
                docker_client=self.client.docker_client, container=container, state=rebound
            )
        )

    def state(self, native: SandboxSession) -> dict[str, object]:
        if not isinstance(native.state, DockerSandboxSessionState):
            raise ConflictError("The environment has an unsupported native backend")
        grants = native.state.manifest.extra_path_grants
        if len(grants) != 1 or grants[0].host_path is None or grants[0].path != SANDBOX_WORKDIR:
            raise ConflictError("The sandbox has no authoritative persistent mount")
        payload = self.client.serialize_session_state(native.state)
        payload["opsmesh_authority"] = {
            "labels": native.state.labels,
            "host_digest": path_digest(Path(grants[0].host_path)),
        }
        return payload

    async def delete_state(self, payload: dict[str, object]) -> None:
        """Delete an identified resource without requiring a still-readable mount.

        Only the lifecycle owner may call this with its encrypted stored state.
        Directory deletion or revocation must not make container cleanup impossible.
        No command is started and no directory is mounted by this path.
        """
        native_payload = {
            key: value for key, value in payload.items() if key != "opsmesh_authority"
        }
        state = self.client.deserialize_session_state(native_payload)
        if not isinstance(state, DockerSandboxSessionState):
            raise ConflictError("The environment has an unsupported native backend")
        container = await asyncio.to_thread(self.client.get_container, state.container_id)
        if container is None:
            # The current manifest uses a bind grant and owns no Docker volumes.
            # Absence is a confirmed idempotent deletion, including a crash after
            # SDK delete but before committing the product receipt.
            if any(isinstance(entry, Mount) for _, entry in state.manifest.iter_entries()):
                raise ConflictError("The missing sandbox has volume cleanup to reconcile")
            return
        await asyncio.to_thread(container.reload)
        labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
        if (
            container.id != state.container_id
            or not state.labels
            or any(labels.get(key) != value for key, value in state.labels.items())
        ):
            raise ConflictError("The native environment identity or labels changed")
        await self.client.delete(
            SandboxSession(
                DockerSandboxSession(
                    docker_client=self.client.docker_client,
                    container=container,
                    state=state,
                )
            )
        )

    async def execute(
        self, native: SandboxSession, action: str, arguments: dict[str, Any]
    ) -> dict[str, object]:
        if action in {"create", "resume"}:
            await native.start()
            return {}
        if action == "close":
            await self.client.delete(native)
            return {}
        if action == "command":
            # Cancelling DockerSandboxSession.exec only cancels its asyncio wait;
            # the Docker executor thread can keep running the command. Keep the
            # native timeout alive and wait for its result before acknowledging.
            command = asyncio.create_task(
                native.exec(
                    "python3",
                    "-I",
                    "-c",
                    helper_source("command"),
                    SANDBOX_WORKDIR,
                    json.dumps(arguments.get("environment", {})),
                    *arguments["command"],
                    shell=False,
                    timeout=float(arguments.get("timeout_seconds", 60)),
                )
            )
            cancelled = False
            while not command.done():
                try:
                    await asyncio.shield(command)
                except asyncio.CancelledError:
                    cancelled = True
            result = command.result()
            payload: dict[str, object] = {
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "exit_code": result.exit_code,
            }
            if cancelled:
                raise SandboxCommandCancelled(payload)
            return payload
        if action == "read":
            try:
                return {"content_base64": await read_file(native, arguments["path"])}
            except WorkspaceReadNotFoundError:
                return {"missing": True}
        if action == "write":
            return {
                "bytes_written": await write_file(
                    native, arguments["path"], arguments["content_base64"]
                )
            }
        raise ValueError("Unsupported sandbox operation")

    def close(self) -> None:
        if self._client is not None:
            self._client.docker_client.close()
            self._client = None


def path_digest(path: Path) -> str:
    return sha256(str(path.resolve()).encode()).hexdigest()


def validate_container(container: Any, state: DockerSandboxSessionState, path: Path) -> None:
    container.reload()
    attributes = container.attrs
    labels = attributes.get("Config", {}).get("Labels", {}) or {}
    host = attributes.get("HostConfig", {})
    if container.id != state.container_id or any(
        labels.get(key) != value for key, value in state.labels.items()
    ):
        raise ConflictError("The native environment identity or labels changed")
    if host.get("Privileged"):
        raise ConflictError("The native environment isolation changed")
    matches = [
        mount
        for mount in attributes.get("Mounts", [])
        if mount.get("Destination") == SANDBOX_WORKDIR
    ]
    if (
        len(matches) != 1
        or matches[0].get("Type") != "bind"
        or matches[0].get("Source") != str(path)
        or not matches[0].get("RW")
        or any(
            mount.get("Type") == "bind" and mount.get("Destination") != SANDBOX_WORKDIR
            for mount in attributes.get("Mounts", [])
        )
    ):
        raise ConflictError("The native environment directory mount changed")


class AgentSandboxState(SandboxSessionState):
    type: Literal["opsmesh"] = "opsmesh"
    environment_id: UUID
    manifest: Manifest = Field(default_factory=workspace_manifest)
    snapshot: NoopSnapshot = Field(default_factory=lambda: NoopSnapshot(id="borrowed"))
    workspace_root_ready: bool = True


class AgentSandboxClient(BaseSandboxClient[Any]):
    backend_id = "opsmesh"

    def __init__(
        self,
        owner: SandboxOwner,
        backend: SandboxBackend,
        *,
        operation_id: Callable[[UUID], str] | None = None,
        operation_scope: Callable[[UUID], str] | None = None,
    ) -> None:
        self.owner, self.backend = owner, backend
        self.operation_id = operation_id or (lambda run_id: f"{run_id}:sandbox:{uuid4()}")
        self.operation_scope = operation_scope or self.operation_id

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: Any = None,
    ) -> SandboxSession:
        raise ForbiddenError("Create a persistent environment before running a SandboxAgent")

    async def delete(self, session: SandboxSession) -> SandboxSession:
        # This client owns a borrowed reference, not the persistent container.
        await session.shutdown()
        return session

    def deserialize_session_state(self, state: dict[str, Any]) -> SandboxSessionState:
        return AgentSandboxState.model_validate(state)

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        reference = AgentSandboxState.model_validate(state.model_dump())
        await asyncio.to_thread(self._authorize, reference)
        return SandboxSession(AgentSandboxSession(self, reference))

    def _authorize(self, state: AgentSandboxState) -> dict[str, object]:
        with self.owner.sessions.begin() as session:
            run = authorize_run(session, state.session_id)
            environment = session.get(SandboxEnvironment, state.environment_id)
            if (
                run.status != "running"
                or run.environment_id != state.environment_id
                or environment is None
                or environment.owner_user_id != run.user_id
                or environment.active_run_id != run.id
                or environment.status != "ready"
                or not environment.encrypted_state
            ):
                raise ForbiddenError("The Run no longer owns this environment")
            return self.owner.cipher.decrypt_payload(environment.encrypted_state)

    async def execute(
        self, state: AgentSandboxState, action: str, arguments: dict[str, Any], *, occurrence: int
    ) -> dict[str, object]:
        fingerprint = payload_digest({"action": action, "arguments": arguments})
        operation_id = (
            f"{self.operation_id(UUID(str(state.session_id)))}:"
            f"{_operation_target(action, arguments)}:{occurrence}"
        )

        def claim() -> tuple[dict[str, object], dict[str, object] | None]:
            native_state = self._authorize(state)
            with self.owner.sessions.begin() as session:
                run = authorize_run(session, state.session_id, lock=True)
                if run.status != "running" or run.environment_id != state.environment_id:
                    raise ForbiddenError("The Run no longer owns an active sandbox operation")
                previous = session.get(ExecutionOperation, operation_id, with_for_update=True)
                if previous:
                    if previous.fingerprint != fingerprint or previous.status != "completed":
                        raise ConflictError("The sandbox occurrence requires reconciliation")
                    return native_state, self.owner.cipher.decrypt_payload(
                        previous.encrypted_result or ""
                    )
                session.add(
                    ExecutionOperation(
                        id=operation_id,
                        run_id=run.id,
                        kind="sandbox",
                        status="started",
                        fingerprint=fingerprint,
                    )
                )
                return native_state, None

        native_state, cached = await asyncio.to_thread(claim)
        if cached is not None:
            return cached

        def complete(value: dict[str, object] | None) -> None:
            with self.owner.sessions.begin() as session:
                receipt = session.get(ExecutionOperation, operation_id, with_for_update=True)
                assert receipt is not None
                if receipt.status == "completed":
                    return
                receipt.status = "completed" if value is not None else "uncertain"
                if value is not None:
                    encrypted = self.owner.cipher.encrypt_payload(value)
                    receipt.encrypted_result, receipt.key_id = (
                        encrypted.ciphertext,
                        encrypted.key_id,
                    )

        try:
            native = await self.backend.resume(native_state)
            result = await self.backend.execute(native, action, arguments)
            if len(str(result).encode()) > 1_000_000:
                raise InvalidArgumentError("The sandbox tool output exceeds its transfer bound")
            await asyncio.to_thread(complete, result)
            return result
        except SandboxCommandCancelled as cancelled:
            await asyncio.to_thread(complete, cancelled.result)
            raise
        except BaseException:
            await asyncio.shield(asyncio.create_task(asyncio.to_thread(complete, None)))
            raise


def _operation_target(action: str, arguments: dict[str, Any]) -> str:
    # A file's contents and resolved credentials must remain fingerprint checks,
    # so changing them on retry cannot create a different effect identity.
    target = arguments["command"] if action == "command" else arguments["path"]
    return payload_digest({"action": action, "target": target})


class AgentSandboxSession(BaseSandboxSession):
    state: AgentSandboxState

    def __init__(self, client: AgentSandboxClient, state: AgentSandboxState) -> None:
        self.client, self.state = client, state
        self.closed = False
        self._occurrences: dict[tuple[str, str], int] = {}

    async def start(self) -> None:
        await asyncio.to_thread(self.client._authorize, self.state)
        self.closed = False
        await self.apply_manifest()
        self.state.workspace_root_ready = True

    async def _execute(self, action: str, arguments: dict[str, Any]) -> dict[str, object]:
        scope = self.client.operation_scope(UUID(str(self.state.session_id)))
        target = scope, _operation_target(action, arguments)
        occurrence = self._occurrences.get(target, 0) + 1
        self._occurrences[target] = occurrence
        return await self.client.execute(self.state, action, arguments, occurrence=occurrence)

    async def stop(self) -> None:
        self.closed = True

    async def shutdown(self) -> None:
        self.closed = True

    async def running(self) -> bool:
        if self.closed:
            return False
        await asyncio.to_thread(self.client._authorize, self.state)
        return True

    async def _exec_internal(
        self, *command: str | Path, timeout: float | None = None
    ) -> ExecResult:
        result = await self._execute(
            "command",
            {
                "command": [str(value) for value in command],
                "timeout_seconds": min(timeout or 60, 300),
                "environment": await self.state.manifest.environment.resolve(),
            },
        )
        return ExecResult(
            stdout=str(result["stdout"]).encode(),
            stderr=str(result["stderr"]).encode(),
            exit_code=int(cast(int, result["exit_code"])),
        )

    @staticmethod
    def _path(path: Path) -> str:
        try:
            return (
                path.relative_to("/workspace").as_posix() if path.is_absolute() else path.as_posix()
            )
        except ValueError:
            raise ForbiddenError(
                "Sandbox file operations are limited to the working directory"
            ) from None

    async def read(self, path: Path, *, user: User | str | None = None) -> io.IOBase:
        if user is not None:
            raise ForbiddenError("The sandbox identity cannot be overridden")
        result = await self._execute("read", {"path": self._path(path)})
        if result.get("missing"):
            raise WorkspaceReadNotFoundError(path=path)
        return io.BytesIO(base64.b64decode(str(result["content_base64"]), validate=True))

    async def write(self, path: Path, data: io.IOBase, *, user: User | str | None = None) -> None:
        if user is not None:
            raise ForbiddenError("The sandbox identity cannot be overridden")
        value = data.read(500001)
        if not isinstance(value, bytes) or len(value) > 500000:
            raise InvalidArgumentError("The sandbox file exceeds its transfer bound")
        await self.mkdir(path.parent, parents=True)
        await self._execute(
            "write",
            {
                "path": self._path(path),
                "content_base64": base64.b64encode(value).decode(),
            },
        )

    async def persist_workspace(self) -> io.IOBase:
        raise InvalidArgumentError("The persistent environment has its own lifecycle")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        raise InvalidArgumentError("An existing persistent environment cannot be hydrated by a Run")
