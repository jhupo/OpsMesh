"""Approved, bounded Docker process effects and per-invocation private RPC."""

import asyncio
import json
import shutil
from typing import Any

import docker
from opsmesh_sdk.manifest import ExecutionPolicy

from opsmesh.extensions.contracts import HostRequest
from opsmesh.shared.errors import ConflictError

MAX_BYTES = 2097152


class DockerManagedWorker:
    def start(
        self,
        *,
        deployment_id: str,
        instance_id: str,
        artifact: str,
        namespace: str,
        policy: ExecutionPolicy,
    ) -> str:
        client = docker.from_env()
        name = f"opsmesh-extension-{deployment_id}"
        labels = {"opsmesh.deployment": deployment_id, "opsmesh.instance": instance_id}
        try:
            try:
                container = client.containers.get(name)
            except docker.errors.NotFound:
                container = client.containers.create(
                    artifact,
                    [
                        "python",
                        "-I",
                        "-m",
                        "opsmesh_sdk.runtime.managed",
                        "--namespace",
                        namespace,
                        "--instance",
                        instance_id,
                        "--concurrency",
                        str(policy.max_concurrency),
                    ],
                    name=name,
                    labels=labels,
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges"],
                    tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777"},
                    mem_limit=policy.memory_mb * 1048576,
                    memswap_limit=policy.memory_mb * 1048576,
                    nano_cpus=int(policy.cpus * 1e9),
                    pids_limit=128,
                )
            if (
                any(container.labels.get(k) != v for k, v in labels.items())
                or container.image is None
                or container.image.id != artifact
            ):
                raise ConflictError("The existing process does not match its frozen deployment")
            container.reload()
            if container.status == "created":
                container.start()
            elif container.status != "running":
                raise ConflictError(
                    "The resident process stopped; explicit reconciliation is required"
                )
            # Readiness checks the SDK listener, not merely a running container.
            import time

            for _ in range(100):
                probe = container.exec_run(
                    [
                        "python",
                        "-I",
                        "-c",
                        "from pathlib import Path; import sys; "
                        "sys.exit(not Path('/tmp/opsmesh-worker.sock').is_socket())",
                    ]
                )
                if probe.exit_code == 0:
                    return str(container.id)
                container.reload()
                if container.status != "running":
                    break
                time.sleep(0.1)
            raise ConflictError("The resident process did not become ready")
        finally:
            client.close()

    def running(self, container_id: str, deployment_id: str) -> bool:
        client = docker.from_env()
        try:
            try:
                container = client.containers.get(container_id)
            except docker.errors.NotFound:
                return False
            if container.labels.get("opsmesh.deployment") != deployment_id:
                raise ConflictError("Resident container identity changed")
            return bool(container.status == "running")
        finally:
            client.close()

    def stop(self, deployment_id: str) -> None:
        client = docker.from_env()
        try:
            try:
                container = client.containers.get(f"opsmesh-extension-{deployment_id}")
            except docker.errors.NotFound:
                return
            if container.labels.get("opsmesh.deployment") != deployment_id:
                raise ConflictError("Refusing to stop another deployment's container")
            if container.status == "paused":
                container.unpause()
            container.stop(timeout=10)
            container.remove()
        finally:
            client.close()

    async def invoke(
        self,
        *,
        container_id: str,
        envelope: dict[str, Any],
        host: HostRequest,
    ) -> dict[str, Any]:
        executable = shutil.which("docker")
        if not executable:
            raise ConflictError("The resident invocation transport is unavailable")
        process = await asyncio.create_subprocess_exec(
            executable,
            "exec",
            "-i",
            container_id,
            "python",
            "-I",
            "-m",
            "opsmesh_sdk.runtime.managed",
            "--connect",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_BYTES + 1,
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            async with asyncio.timeout(float(envelope["deadline_seconds"]) + 5):
                encoded = json.dumps(envelope, allow_nan=False).encode() + b"\n"
                if len(encoded) > MAX_BYTES:
                    raise ConflictError("Resident envelope exceeds its byte bound")
                process.stdin.write(encoded)
                await process.stdin.drain()
                for _ in range(1025):
                    raw = await process.stdout.readline()
                    if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
                        raise ConflictError("The resident protocol ended without a bounded receipt")
                    message = json.loads(raw)
                    if message.get("type") == "result":
                        return dict(message["result"])
                    if message.get("type") != "request":
                        raise ConflictError("Unexpected resident protocol frame")
                    answer = json.dumps(await host(message), allow_nan=False).encode() + b"\n"
                    if len(answer) > MAX_BYTES:
                        raise ConflictError("Resident host response exceeds its byte bound")
                    process.stdin.write(answer)
                    await process.stdin.drain()
                raise ConflictError("Resident host occurrence bound exceeded")
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), 5)
            except (TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                await asyncio.shield(process.wait())
