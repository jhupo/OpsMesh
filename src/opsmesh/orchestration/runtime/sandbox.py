"""Native Docker sandbox lifecycle, without an OpsMesh host pool or agent loop."""

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import docker
from agents.sandbox import Manifest, SandboxPathGrant
from agents.sandbox.sandboxes.docker import (
    DockerSandboxClient,
    DockerSandboxClientOptions,
    DockerSandboxSession,
    DockerSandboxSessionState,
)
from agents.sandbox.session import SandboxSession

from opsmesh.orchestration.runtime.contracts import SandboxDirectoryAuthority
from opsmesh.orchestration.runtime.files import SANDBOX_WORKDIR, read_file, write_file
from opsmesh.shared.errors import ConflictError


class SandboxCommandCancelled(asyncio.CancelledError):
    """Cancellation acknowledged after the native command returned a known result."""

    def __init__(self, result: dict[str, object]) -> None:
        super().__init__("The native command completed while cancellation was pending")
        self.result = result


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
                    "import os,sys; os.chdir(sys.argv[1]); os.execvp(sys.argv[2],sys.argv[2:])",
                    SANDBOX_WORKDIR,
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
            return {"content_base64": await read_file(native, arguments["path"])}
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


def trusted_manifest(path: Path) -> Manifest:
    return Manifest(
        root="/tmp/opsmesh_runtime",
        extra_path_grants=(
            SandboxPathGrant(path=SANDBOX_WORKDIR, host_path=str(path), read_only=False),
        ),
    )


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
