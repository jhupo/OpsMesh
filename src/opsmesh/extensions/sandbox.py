"""Bounded package stdio protocol over explicitly authorized environment operations."""

import asyncio
import json
import shutil
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from opsmesh.extensions.contracts import HostRequest, InvocationCancelled
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.serialization import payload_digest

MAX_MESSAGE_BYTES = 2_097_152


class PackageEnvironments(Protocol):
    """Environment owner authorizes and receipts each command before external I/O."""

    async def claim(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        configuration_id: UUID | None,
        invocation_id: UUID,
        operation_id: str,
        artifact: str,
        fingerprint: str,
        command: list[str],
        timeout_seconds: int,
    ) -> tuple[bool, dict[str, Any]]: ...

    async def container_id(self, state: dict[str, Any]) -> str: ...

    async def complete(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        invocation_id: UUID,
        operation_id: str,
        result: dict[str, Any] | None,
    ) -> None: ...


class PackageSandbox:
    def __init__(self, environments: PackageEnvironments) -> None:
        self.environments = environments

    async def execute(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        configuration_id: UUID | None,
        operation_id: str,
        artifact: str,
        envelope: dict[str, Any],
        deadline: datetime,
        host: HostRequest,
        memory_mb: int,
        cpus: float,
    ) -> dict[str, Any]:
        timeout = max(1, int((deadline - datetime.now(UTC)).total_seconds()))
        invocation_id = UUID(envelope["subject"]["invocation_id"])
        command = [
            "timeout",
            "--signal=TERM",
            "--kill-after=2",
            str(timeout),
            "python",
            "-I",
            "-m",
            "opsmesh_sdk.runtime.runner",
        ]
        claimed, state = await self.environments.claim(
            environment_id=environment_id,
            principal_id=principal_id,
            configuration_id=configuration_id,
            invocation_id=invocation_id,
            operation_id=operation_id,
            artifact=artifact,
            fingerprint=payload_digest(
                {
                    "artifact": artifact,
                    "envelope": {
                        key: value for key, value in envelope.items() if key != "deadline_seconds"
                    },
                }
            ),
            command=command,
            timeout_seconds=timeout,
        )
        if not claimed:
            return state
        result = None
        try:
            container_id = await self.environments.container_id(state)
            executable = shutil.which("docker")
            if not executable or not container_id:
                raise ConflictError("The native stdio transport is unavailable")
            limits = await asyncio.create_subprocess_exec(
                executable,
                "update",
                "--memory",
                f"{memory_mb}m",
                "--memory-swap",
                f"{memory_mb}m",
                "--cpus",
                str(cpus),
                "--pids-limit",
                "64",
                container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                limit_status = await asyncio.wait_for(limits.wait(), timeout=15)
            finally:
                if limits.returncode is None:
                    limits.kill()
                    await limits.wait()
            if limit_status != 0:
                raise ConflictError("The sandbox resource limits could not be applied")
            process = await asyncio.create_subprocess_exec(
                executable,
                "exec",
                "-i",
                container_id,
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_MESSAGE_BYTES + 1,
            )
            assert process.stdin is not None and process.stdout is not None
            stdin, stdout = process.stdin, process.stdout

            async def exchange() -> dict[str, Any]:
                stdin.write(json.dumps(envelope, allow_nan=False).encode() + b"\n")
                await stdin.drain()
                count = 0
                while True:
                    raw = await stdout.readline()
                    if not raw or len(raw) > MAX_MESSAGE_BYTES:
                        raise ConflictError("The package protocol ended without a bounded result")
                    message = json.loads(raw)
                    if message.get("type") == "result":
                        stdin.close()
                        await process.wait()
                        if process.returncode != 0:
                            raise ConflictError("The package process did not exit successfully")
                        return dict(message["result"])
                    count += 1
                    if message.get("type") != "request" or count > 1024:
                        raise ConflictError("The package exceeded its host request bound")
                    answer = await host(message)
                    encoded = json.dumps(answer, allow_nan=False).encode()
                    if len(encoded) > MAX_MESSAGE_BYTES:
                        raise ConflictError("The host response exceeded its byte bound")
                    stdin.write(encoded + b"\n")
                    await stdin.drain()

            task = asyncio.create_task(asyncio.wait_for(exchange(), timeout=timeout + 5))
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                result = task.result()
            finally:
                # The in-container timeout owns the process tree, including cancellation.
                stdin.close()
                await asyncio.shield(process.wait())
            if cancelled:
                raise InvocationCancelled(result)
            return result
        finally:
            await asyncio.shield(
                self.environments.complete(
                    environment_id=environment_id,
                    principal_id=principal_id,
                    invocation_id=invocation_id,
                    operation_id=operation_id,
                    result=result,
                )
            )
