"""Launch complete Agent runs inline, in child processes, or in constrained containers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import make_url

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI
from opsmesh.infrastructure.run_workers.kubernetes.lifecycle import KubernetesRunWorkers
from opsmesh.infrastructure.run_workers.status import WorkerMode
from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus as WorkerProcessStatus
from opsmesh.modules.runtime.api import AgentRunView

from .configuration import WorkerLaunchConfiguration


class RunWorkerLauncher:
    def __init__(
        self,
        runtime: Any,
        configuration: WorkerLaunchConfiguration,
        *,
        mode: WorkerMode = "inline",
        docker_image: str | None = None,
        cpu: float = 1.0,
        memory_mb: int = 512,
        pids_limit: int = 128,
        network: str = "bridge",
        workdir: Path | None = None,
        environment_allowlist: tuple[str, ...] = (),
        kubernetes_api: KubernetesAPI | None = None,
    ) -> None:
        if cpu <= 0 or memory_mb <= 0 or pids_limit <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Worker resource limits must be positive")
        if mode not in {"inline", "subprocess", "docker", "kubernetes"}:
            raise OpsMeshError("CONFIG_INVALID", "Unknown worker mode")
        if mode == "docker" and (
            not docker_image or network in {"host", "container"} or network.startswith("container:")
        ):
            raise OpsMeshError(
                "CONFIG_INVALID", "Docker workers require an image and isolated network mode"
            )
        self.runtime, self.configuration, self.mode = runtime, configuration, mode
        self.docker_image, self.cpu, self.memory_mb, self.pids_limit = (
            docker_image,
            cpu,
            memory_mb,
            pids_limit,
        )
        self.network, self.workdir = network, (workdir or Path.cwd()).resolve()
        self.environment_allowlist = environment_allowlist
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[AgentRunView]] = {}
        self._containers: dict[str, str] = {}
        self._tokens: dict[str, str] = {}
        self._lease_tasks: dict[str, asyncio.Task[None]] = {}
        self._launch_lock = asyncio.Lock()
        self._kubernetes: KubernetesRunWorkers | None = None
        if mode == "kubernetes":
            if not configuration.kubernetes_worker or not docker_image:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Kubernetes workers need configuration and image"
                )
            if not make_url(configuration.database_url).drivername.startswith("postgresql"):
                raise OpsMeshError("CONFIG_INVALID", "Kubernetes workers require shared PostgreSQL")
            self._kubernetes = KubernetesRunWorkers(
                runtime,
                kubernetes_api or KubernetesAPI(configuration.kubernetes_worker),
                image=docker_image,
                cpu=cpu,
                memory_mb=memory_mb,
                data_directory=configuration.data_directory,
            )

    def _environment(self) -> dict[str, str]:
        basic = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        }
        names = basic | set(self.environment_allowlist)
        environment = {key: value for key, value in os.environ.items() if key in names}
        environment["OPSMESH_WORKER_SETTINGS"] = self.configuration.settings_payload(self.mode)
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def command(self, run_id: str) -> tuple[str, ...]:
        """Construct reviewable argv; no user-provided shell command is evaluated."""
        worker = ("-m", "opsmesh.entrypoints.run_worker", "--run-id", run_id)
        if self.mode == "subprocess":
            return (sys.executable, *worker)
        if self.mode != "docker":
            raise OpsMeshError("CONFIG_INVALID", "Inline runs have no child-process command")
        identity = hashlib.sha256(run_id.encode()).hexdigest()[:16]
        name = f"opsmesh-run-{identity}-{self._tokens.get(run_id, 'preview')[:12]}"
        self._containers[run_id] = name
        command = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--entrypoint",
            "python",
            "--name",
            name,
            "--cpus",
            str(self.cpu),
            "--memory",
            f"{self.memory_mb}m",
            "--pids-limit",
            str(self.pids_limit),
            "--network",
            self.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m",
            "--workdir",
            "/var/lib/opsmesh",
            "--mount",
            f"type=bind,source={self.configuration.data_directory.resolve()},target=/var/lib/opsmesh",
            "--env",
            "OPSMESH_WORKER_SETTINGS",
            "--env",
            "OPSMESH_RUN_LAUNCH_TOKEN",
            "--env",
            "PYTHONUNBUFFERED",
        ]
        for key in self.environment_allowlist:
            command.extend(("--env", key))
        command.extend((str(self.docker_image), *worker))
        return tuple(command)

    async def launch(self, run_id: str) -> None:
        if self._kubernetes is not None:
            await self._kubernetes.launch(run_id)
            return
        async with self._launch_lock:
            status = await self.poll(run_id)
            if status is not None and status.status == "running":
                return
            if status is not None and status.exit_code:
                current = await self.runtime.worker_exited(
                    run_id, status.exit_code, launch_token=status.launch_token
                )
                if current.status not in {"queued", "resuming"}:
                    return
            if self.mode == "inline":
                self._tasks[run_id] = self.runtime.start(run_id)
                return
            if self.mode == "docker" and shutil.which("docker") is None:
                raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Docker executable is unavailable")
            token = uuid4().hex
            if not await self.runtime.claim_worker_launch(run_id, token):
                return
            self._tokens[run_id] = token
            try:
                process = await self._spawn_process(run_id, token)
            except BaseException:
                await self.runtime.worker_exited(run_id, 1, launch_token=token)
                raise
            self._processes[run_id] = process
            self._lease_tasks[run_id] = asyncio.create_task(
                self._renew_launch(run_id, token, process)
            )

    async def _spawn_process(self, run_id: str, token: str) -> asyncio.subprocess.Process:
        logs = self.configuration.data_directory / "worker-logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{hashlib.sha256(run_id.encode()).hexdigest()}.log"
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        environment = dict(self._environment(), OPSMESH_RUN_LAUNCH_TOKEN=token)
        with log.open("ab") as output:
            return await asyncio.create_subprocess_exec(
                *self.command(run_id),
                cwd=self.workdir,
                env=environment,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                **options,
            )

    async def _renew_launch(
        self, run_id: str, token: str, process: asyncio.subprocess.Process
    ) -> None:
        while process.returncode is None:
            if not await self.runtime.renew_worker_launch(run_id, token):
                return  # Run acquired its execution lease, or a replacement owns the launch.
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                continue

    async def poll(self, run_id: str) -> WorkerProcessStatus | None:
        if self._kubernetes is not None:
            return await self._kubernetes.poll(run_id)
        if self.mode == "inline":
            task = self._tasks.get(run_id)
            if task is None:
                return None
            code = None
            if task.done():
                code = int(task.cancelled() or task.exception() is not None)
                if code == 0:
                    code = int(task.result().status in {"failed", "blocked"})
            return WorkerProcessStatus(
                run_id=run_id,
                mode=self.mode,
                status="running" if not task.done() else "exited",
                exit_code=code,
            )
        process = self._processes.get(run_id)
        if process is None:
            return None
        return WorkerProcessStatus(
            run_id=run_id,
            mode=self.mode,
            pid=process.pid,
            status="running" if process.returncode is None else "exited",
            exit_code=process.returncode,
            launch_token=self._tokens.get(run_id),
        )

    async def close(self) -> None:
        if self._kubernetes is not None:
            await self._kubernetes.close()
            return
        for task in self._lease_tasks.values():
            task.cancel()
        await asyncio.gather(*self._lease_tasks.values(), return_exceptions=True)
        if self.mode == "inline":
            await self.runtime.close()
            return
        for run_id, process in self._processes.items():
            if process.returncode is not None:
                continue
            await self.runtime.cancel(run_id, launch_token=self._tokens.get(run_id))
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
                continue
            except TimeoutError:
                pass
            if self.mode == "docker":
                remover = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "--force",
                    self._containers[run_id],
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(remover.wait(), timeout=10)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    async def reap(self) -> None:
        """Report exited owners, then release process and result references."""
        if self._kubernetes is not None:
            await self._kubernetes.reap()
            return
        async with self._launch_lock:
            for run_id, task in tuple(self._tasks.items()):
                if task.done():
                    if not task.cancelled():
                        task.exception()
                    self._tasks.pop(run_id, None)
            for run_id, process in tuple(self._processes.items()):
                if process.returncode is None:
                    continue
                if process.returncode:
                    await self.runtime.worker_exited(
                        run_id, process.returncode, launch_token=self._tokens.get(run_id)
                    )
                lease = self._lease_tasks.pop(run_id, None)
                if lease is not None:
                    lease.cancel()
                    await asyncio.gather(lease, return_exceptions=True)
                self._processes.pop(run_id, None)
                self._tokens.pop(run_id, None)
                self._containers.pop(run_id, None)

    async def logs(self, run_id: str) -> str:
        if self._kubernetes is not None:
            return await self._kubernetes.logs(run_id)
        path = (
            self.configuration.data_directory
            / "worker-logs"
            / (hashlib.sha256(run_id.encode()).hexdigest() + ".log")
        )
        if not path.is_file():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 1_000_000))
            return stream.read(1_000_000).decode("utf-8", errors="replace")

    async def cancel(self, run_id: str) -> None:
        if self._kubernetes is not None:
            await self._kubernetes.cancel(run_id)
        else:
            await self.runtime.cancel(run_id)
