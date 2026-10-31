"""Enforce Docker CPU, memory and process limits at native SDK container allocation."""

import math
from typing import Any

from opsmesh.framework.errors import OpsMeshError


class DockerResourcePolicy:
    def __init__(self, values: dict[str, Any]) -> None:
        unknown = set(values) - {"cpus", "memory_bytes", "pids_limit"}
        if unknown:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED",
                "Unsupported native Docker resource limit",
                details={"fields": sorted(unknown)},
            )
        self.options: dict[str, Any] = {}
        for key, value in values.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise OpsMeshError(
                    "CONFIG_INVALID", "Docker resource limits must be finite and positive"
                )
            if key == "cpus":
                nanos = int(value * 1_000_000_000)
                if nanos < 1:
                    raise OpsMeshError("CONFIG_INVALID", "CPU limit is below Docker resolution")
                self.options["nano_cpus"] = nanos
            elif not isinstance(value, int):
                raise OpsMeshError("CONFIG_INVALID", "Memory and process limits must be integers")
            elif key == "memory_bytes":
                if value < 6 * 1024 * 1024:
                    raise OpsMeshError(
                        "CONFIG_INVALID", "Docker memory limit must be at least 6 MiB"
                    )
                self.options.update(mem_limit=value, memswap_limit=value)
            else:
                self.options["pids_limit"] = value

    def verify(self, container: Any) -> None:
        if not self.options:
            return
        container.reload()
        host = container.attrs.get("HostConfig", {})
        fields = {
            "nano_cpus": "NanoCpus",
            "mem_limit": "Memory",
            "memswap_limit": "MemorySwap",
            "pids_limit": "PidsLimit",
        }
        if any(host.get(fields[key]) != value for key, value in self.options.items()):
            raise OpsMeshError(
                "RESOURCE_POLICY_MISMATCH",
                "Docker container limits differ from the requested policy",
            )


class LimitedDockerContainers:
    def __init__(self, containers: Any, policy: DockerResourcePolicy) -> None:
        self.containers, self.policy = containers, policy

    def create(self, **kwargs: Any) -> Any:
        container = self.containers.create(**kwargs, **self.policy.options)
        try:
            self.policy.verify(container)
        except BaseException:
            container.remove(force=True)
            raise
        return container

    def get(self, *args: Any, **kwargs: Any) -> Any:
        container = self.containers.get(*args, **kwargs)
        self.policy.verify(container)
        return container

    def __getattr__(self, name: str) -> Any:
        return getattr(self.containers, name)


class LimitedDockerClient:
    def __init__(self, client: Any, policy: DockerResourcePolicy) -> None:
        self.client = client
        self.containers = LimitedDockerContainers(client.containers, policy)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


def docker_client(options: dict[str, Any], *, client: Any = None) -> tuple[Any, Any]:
    native = dict(options)
    limits = native.pop("resource_limits", {})
    if not isinstance(limits, dict):
        raise OpsMeshError("CONFIG_INVALID", "Docker resource_limits must be an object")
    policy = DockerResourcePolicy(limits)
    if set(native) - {"image", "exposed_ports", "network_mode", "labels"}:
        raise OpsMeshError("FEATURE_UNSUPPORTED", "Unsupported native Docker client option")
    try:
        from agents.sandbox.sandboxes.docker import DockerSandboxClient, DockerSandboxClientOptions
        from docker import from_env  # type: ignore[import-untyped]
    except ImportError as exc:
        raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Install the SDK docker extra") from exc
    try:
        configured = DockerSandboxClientOptions(**native)
    except (TypeError, ValueError) as error:
        raise OpsMeshError("CONFIG_INVALID", "Native Docker options are invalid") from error
    return DockerSandboxClient(
        LimitedDockerClient(client if client is not None else from_env(), policy)
    ), configured
