import subprocess

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)


class DockerCliRuntimeClient(DockerRuntimeClient):
    def create_container(self, request: RuntimeCreateRequest) -> str:
        labels = {
            "opsmesh.workspace_id": request.workspace_id,
            **request.labels,
        }
        if request.runtime_id is not None:
            labels["opsmesh.runtime_id"] = request.runtime_id
        if request.runtime_space_id is not None:
            labels["opsmesh.runtime_space_id"] = request.runtime_space_id
        command = [
            "docker",
            "create",
            "--name",
            request.name,
            "--cpus",
            str(request.limits.cpu_count),
            "--memory",
            f"{request.limits.memory_mb}m",
            "--pids-limit",
            str(request.limits.max_processes),
            "--storage-opt",
            f"size={request.limits.disk_mb}m",
            "--network",
            "none" if request.network_disabled else "bridge",
        ]
        for capability in request.hardening.cap_drop:
            command.extend(["--cap-drop", capability])
        for security_opt in request.hardening.security_opt:
            command.extend(["--security-opt", security_opt])
        if request.hardening.read_only_rootfs:
            command.append("--read-only")
        for tmpfs in request.hardening.tmpfs:
            command.extend(
                [
                    "--tmpfs",
                    f"{tmpfs.target}:{tmpfs.mode},size={tmpfs.size_mb}m",
                ]
            )
        if request.hardening.user is not None:
            command.extend(["--user", request.hardening.user])
        for key, value in sorted(labels.items()):
            command.extend(["--label", f"{key}={value}"])
        for mount in request.mounts:
            options = [
                f"type={mount.mount_type}",
                f"source={mount.source}",
                f"target={mount.target}",
            ]
            if mount.read_only:
                options.append("readonly")
            command.extend(["--mount", ",".join(options)])
        if request.working_dir is not None:
            command.extend(["--workdir", request.working_dir])
        command.extend([request.image, "sleep", "infinity"])
        return self._run(command, timeout_seconds=30).stdout.strip()

    def start_container(self, container_id: str) -> None:
        self._run(["docker", "start", container_id], timeout_seconds=30)

    def stop_container(self, container_id: str) -> None:
        self._run(["docker", "stop", container_id], timeout_seconds=30)

    def remove_container(self, container_id: str) -> None:
        self._run(["docker", "rm", "-f", container_id], timeout_seconds=30)

    def remove_volume(self, volume_name: str) -> None:
        self._run(["docker", "volume", "rm", "-f", volume_name], timeout_seconds=30)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        stdin_data: str | None = None,
    ) -> RuntimeCommandResult:
        docker_command = ["docker", "exec"]
        if stdin_data is not None:
            docker_command.append("-i")
        docker_command.extend([container_id, *command])
        result = self._run(
            docker_command,
            timeout_seconds=timeout_seconds,
            check=False,
            stdin_data=stdin_data,
        )
        return RuntimeCommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        check: bool = True,
        stdin_data: str | None = None,
    ) -> RuntimeCommandResult:
        if stdin_data is None:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        else:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                input=stdin_data,
                text=True,
                timeout=timeout_seconds,
            )
        if check and completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout)
        return RuntimeCommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
