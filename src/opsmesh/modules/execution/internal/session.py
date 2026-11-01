"""Command lifecycle, cancellation and bounded result collection for an execution session."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.execution.api import CommandRequest, CommandResult, ExecutionSpec

from .docker import docker_control
from .processes import bounded_read, command_cwd, terminate_tree


class ProcessSession:
    def __init__(
        self,
        spec: ExecutionSpec,
        *,
        container_id: str | None = None,
        container_control: Callable[..., Awaitable[str]] = docker_control,
    ) -> None:
        self.id = uuid4().hex
        self.spec = spec
        self._root = spec.workspace.resolve()
        self._container = container_id
        self._container_control = container_control
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._reserved: set[str] = set()
        self._cancelled: set[str] = set()
        self._closed = False
        self._dead_container = False

    async def execute(self, request: CommandRequest) -> CommandResult:
        if self._closed or self._dead_container:
            raise OpsMeshError("ENVIRONMENT_UNAVAILABLE", "Execution session is closed")
        if not request.id or not request.argv or any("\x00" in part for part in request.argv):
            raise OpsMeshError("CONFIG_INVALID", "Command id and argument vector are required")
        if request.id in self._reserved:
            raise OpsMeshError("CONFLICT", "Command is already running")
        cwd = command_cwd(self._root, request.cwd)
        argv = list(request.argv)
        environment = {**os.environ, **self.spec.environment}
        if self._container:
            relative = cwd.relative_to(self._root).as_posix()
            inside = "/workspace" if relative == "." else f"/workspace/{relative}"
            argv = ["docker", "exec", "--workdir", inside, self._container, *argv]
            environment = dict(os.environ)
        self._reserved.add(request.id)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            self._reserved.discard(request.id)
            raise OpsMeshError("EXECUTION_START_FAILED", "Command could not start") from error
        except BaseException:
            self._reserved.discard(request.id)
            raise
        self._processes[request.id] = process
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(bounded_read(process.stdout, request.max_output_bytes))
        stderr_task = asyncio.create_task(bounded_read(process.stderr, request.max_output_bytes))
        try:
            async with asyncio.timeout(request.timeout_seconds):
                exit_code = await process.wait()
                stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            if request.id in self._cancelled:
                raise OpsMeshError("EXECUTION_CANCELLED", "Command was cancelled")
            return CommandResult(
                id=request.id,
                exit_code=exit_code,
                stdout=stdout[0].decode(errors="replace"),
                stderr=stderr[0].decode(errors="replace"),
                output_truncated=stdout[1] or stderr[1],
            )
        except (TimeoutError, asyncio.CancelledError) as error:
            await self._terminate(process)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise OpsMeshError("EXECUTION_TIMEOUT", "Command exceeded its timeout") from error
        finally:
            self._processes.pop(request.id, None)
            self._reserved.discard(request.id)
            self._cancelled.discard(request.id)
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        # Killing only docker's CLI would leave the remote exec running. Terminate the container.
        try:
            if self._container and not self._dead_container:
                self._dead_container = True
                await self._container_control("kill", self._container)
        finally:
            await terminate_tree(process)

    async def cancel(self, command_id: str) -> None:
        process = self._processes.get(command_id)
        if process is not None:
            self._cancelled.add(command_id)
            await self._terminate(process)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for command_id in list(self._processes):
            await self.cancel(command_id)
        if self._container:
            await self._container_control("rm", "--force", self._container)
