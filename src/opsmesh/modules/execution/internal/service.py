"""Execution providers validate resource requests before allocating a session."""

from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.execution.api import ExecutionSpec

from .docker import docker_control
from .session import ProcessSession


class LocalExecutionProvider:
    async def create(self, spec: ExecutionSpec) -> ProcessSession:
        if spec.provider != "local" or not spec.trusted_local:
            raise OpsMeshError(
                "AUTHORIZATION_DENIED", "Local execution requires explicit host trust"
            )
        if (
            any(
                value is not None
                for value in (spec.cpu, spec.memory_mb, spec.pids_limit, spec.disk_mb, spec.image)
            )
            or spec.network != "default"
        ):
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED",
                "Local execution cannot enforce requested isolation/resources",
            )
        if spec.workspace.is_symlink() or not spec.workspace.is_dir():
            raise OpsMeshError("PATH_INVALID", "Execution workspace does not exist")
        return ProcessSession(spec)


class DockerExecutionProvider:
    async def create(self, spec: ExecutionSpec) -> ProcessSession:
        if spec.provider != "docker" or not spec.image:
            raise OpsMeshError("CONFIG_INVALID", "Docker provider requires an image")
        if spec.disk_mb is not None:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "This Docker provider cannot guarantee disk quotas"
            )
        if spec.workspace.is_symlink() or not spec.workspace.is_dir() or "," in str(spec.workspace):
            raise OpsMeshError("PATH_INVALID", "Docker workspace mount is invalid")
        name = f"opsmesh-{uuid4().hex}"
        args = [
            "create",
            "--name",
            name,
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(spec.pids_limit or 128),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={spec.workspace.resolve()},target=/workspace",
        ]
        if spec.network == "none":
            args.extend(["--network", "none"])
        if spec.cpu is not None:
            args.extend(["--cpus", str(spec.cpu)])
        if spec.memory_mb is not None:
            args.extend(["--memory", f"{spec.memory_mb}m", "--memory-swap", f"{spec.memory_mb}m"])
        for key, value in spec.environment.items():
            if not key or "=" in key or "\x00" in key + value:
                raise OpsMeshError("CONFIG_INVALID", "Invalid environment variable")
            args.extend(["--env", f"{key}={value}"])
        args.extend(["--entrypoint", "/bin/sh", spec.image, "-c", "while :; do sleep 3600; done"])
        container_id = await docker_control(*args)
        try:
            await docker_control("start", container_id)
        except BaseException:
            await docker_control("rm", "--force", container_id)
            raise
        return ProcessSession(spec, container_id=container_id, container_control=docker_control)
