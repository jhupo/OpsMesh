"""Native SDK Docker allocation receives real Docker API resource controls."""

from types import SimpleNamespace

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_docker import (
    DockerResourcePolicy,
    LimitedDockerClient,
    docker_client,
)


class Containers:
    def __init__(self):
        self.created = []
        self.container = SimpleNamespace(
            attrs={"HostConfig": {}},
            reload=lambda: None,
            remove=lambda **kwargs: self.created.append({"removed": kwargs}),
        )

    def create(self, **kwargs):
        self.created.append(kwargs)
        keys = {
            "nano_cpus": "NanoCpus",
            "mem_limit": "Memory",
            "memswap_limit": "MemorySwap",
            "pids_limit": "PidsLimit",
        }
        self.container.attrs["HostConfig"] = {name: kwargs[key] for key, name in keys.items()}
        return self.container

    def get(self, identifier):
        return self.container


def test_native_container_limits_apply_at_create_and_are_verified_on_resume():
    raw = Containers()
    policy = DockerResourcePolicy({"cpus": 0.5, "memory_bytes": 64 * 1024 * 1024, "pids_limit": 32})
    client = LimitedDockerClient(SimpleNamespace(containers=raw, api="native"), policy)
    container = client.containers.create(image="image", network_mode="none")
    assert raw.created[0] == {
        "image": "image",
        "network_mode": "none",
        "nano_cpus": 500_000_000,
        "mem_limit": 64 * 1024 * 1024,
        "memswap_limit": 64 * 1024 * 1024,
        "pids_limit": 32,
    }
    assert client.api == "native" and client.containers.get("id") is container
    container.attrs["HostConfig"]["PidsLimit"] = 0
    with pytest.raises(OpsMeshError) as error:
        client.containers.get("id")
    assert error.value.code == "RESOURCE_POLICY_MISMATCH"


@pytest.mark.parametrize(
    "limits",
    [
        {"cpus": 0},
        {"cpus": float("nan")},
        {"memory_bytes": 1},
        {"pids_limit": True},
        {"pids_limit": 2.5},
        {"disk_bytes": 1000},
    ],
)
def test_limits_are_validated_without_allocating_a_container(limits):
    with pytest.raises(OpsMeshError):
        DockerResourcePolicy(limits)


def test_unknown_sdk_options_are_not_silently_ignored():
    with pytest.raises(OpsMeshError) as error:
        docker_client({"image": "image", "cpu_shares": 1024})
    assert error.value.code == "FEATURE_UNSUPPORTED"


def test_provider_refusing_limits_removes_unstarted_container():
    raw = Containers()
    create = raw.create

    def ignore(**kwargs):
        container = create(**kwargs)
        container.attrs["HostConfig"]["Memory"] = 0
        return container

    raw.create = ignore
    proxy = LimitedDockerClient(
        SimpleNamespace(containers=raw),
        DockerResourcePolicy({"cpus": 1, "memory_bytes": 64 * 1024 * 1024, "pids_limit": 32}),
    )
    with pytest.raises(OpsMeshError):
        proxy.containers.create(image="image")
    assert raw.created[-1] == {"removed": {"force": True}}
