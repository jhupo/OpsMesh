"""Remote Docker uses endpoint allowlists, mutual TLS and owned client cleanup."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from opsmesh.framework.agents import SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.openai_extensions import RemoteDockerSandboxProvider
from tests.runtime.test_openai_backend import context


def configuration(endpoint="https://docker.example:2376"):
    return SandboxConfiguration(
        provider="remote-docker@1",
        options={
            "endpoint": endpoint,
            "ca_secret_ref": "test:ca",
            "client_cert_secret_ref": "test:cert",
            "client_key_secret_ref": "test:key",
            "docker_options": {
                "image": "python:3.13-slim",
                "network_mode": "none",
                "resource_limits": {
                    "cpus": 0.5,
                    "memory_bytes": 128 * 1024 * 1024,
                    "pids_limit": 64,
                },
            },
        },
    )


@pytest.mark.asyncio
async def test_remote_client_uses_mutual_tls_and_removes_secret_files(monkeypatch):
    created, closed = [], []

    def connect(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(containers=object(), close=lambda: closed.append(True))

    monkeypatch.setattr("docker.DockerClient", connect)
    ctx = context()
    ctx.services["resolve_secret"] = lambda ref: "test credential material"
    factory = RemoteDockerSandboxProvider(allowed_endpoints=("https://docker.example:2376",))
    binding = await factory(configuration(), ctx)
    tls = created[0]["tls"]
    paths = [Path(tls.ca_cert), *(Path(path) for path in tls.cert)]
    assert tls.verify is True and all(path.is_file() for path in paths)
    assert created[0]["base_url"] == "https://docker.example:2376"
    assert binding.options.image == "python:3.13-slim"
    await binding.aclose()
    await binding.aclose()
    assert closed == [True] and not any(path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://docker.example:2375",
        "https://unapproved:2376",
        "https://user:password@docker.example:2376",
    ],
)
async def test_remote_endpoint_rejected_before_secret_or_network_access(endpoint, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No secret resolution or Docker connection is permitted")

    monkeypatch.setattr("docker.DockerClient", forbidden)
    ctx = context()
    ctx.services["resolve_secret"] = forbidden
    with pytest.raises(OpsMeshError):
        await RemoteDockerSandboxProvider(allowed_endpoints=("https://docker.example:2376",))(
            configuration(endpoint), ctx
        )


@pytest.mark.asyncio
async def test_cancelled_connection_is_drained_and_then_closed(monkeypatch):
    import threading

    begun, finish = threading.Event(), threading.Event()
    closed, paths = [], []

    def connect(**kwargs):
        tls = kwargs["tls"]
        paths.extend([Path(tls.ca_cert), *(Path(path) for path in tls.cert)])
        begun.set()
        assert finish.wait(3)
        return SimpleNamespace(containers=object(), close=lambda: closed.append(True))

    monkeypatch.setattr("docker.DockerClient", connect)
    ctx = context()
    ctx.services["resolve_secret"] = lambda ref: "test credential material"
    task = asyncio.create_task(
        RemoteDockerSandboxProvider(allowed_endpoints=("https://docker.example:2376",))(
            configuration(), ctx
        )
    )
    await asyncio.to_thread(begun.wait, 3)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True] and not any(path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", [False, True])
async def test_provider_closes_when_session_construction_fails(monkeypatch, cleanup):
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
        ManagedSandboxSession,
    )

    closed, paths = [], []

    def connect(**kwargs):
        tls = kwargs["tls"]
        paths.extend([Path(tls.ca_cert), *(Path(path) for path in tls.cert)])
        return SimpleNamespace(containers=object(), close=lambda: closed.append(True))

    monkeypatch.setattr("docker.DockerClient", connect)
    ctx = context()
    ctx.services["resolve_secret"] = lambda ref: "test credential material"
    binding = await RemoteDockerSandboxProvider(allowed_endpoints=("https://docker.example:2376",))(
        configuration(), ctx
    )
    with pytest.raises(ValueError if cleanup else OpsMeshError):
        if cleanup:
            await ManagedSandboxSession.resume_cleanup(configuration(), {}, provider=binding)
        else:
            await ManagedSandboxSession.open(
                configuration().model_copy(update={"manifest": {"root": "/forbidden"}}),
                (),
                (),
                provider=binding,
            )
    assert closed == [True] and not any(path.exists() for path in paths)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("OPSMESH_TEST_REMOTE_DOCKER") != "1",
    reason="Explicit authenticated Docker endpoint required",
)
@pytest.mark.asyncio
async def test_real_mutual_tls_docker_workspace_and_resource_limits():
    from opsmesh.application.sandbox_bridge import archive_files
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
        ManagedSandboxSession,
    )

    endpoint = os.environ["OPSMESH_TEST_DOCKER_ENDPOINT"]
    paths = {
        "test:ca": os.environ["OPSMESH_TEST_DOCKER_CA_PATH"],
        "test:cert": os.environ["OPSMESH_TEST_DOCKER_CERT_PATH"],
        "test:key": os.environ["OPSMESH_TEST_DOCKER_KEY_PATH"],
    }
    ctx = context()
    ctx.services["resolve_secret"] = lambda ref: Path(paths[ref]).read_text()
    spec = configuration(endpoint)
    binding = await RemoteDockerSandboxProvider(allowed_endpoints=(endpoint,))(spec, ctx)
    manager = await ManagedSandboxSession.open(
        spec, (("input.txt", b"input", True),), (), context=ctx, provider=binding
    )
    try:
        await manager.session._exec_checked_nonzero("sh", "-c", "printf remote > result.txt")
        files, _ = archive_files(await manager.archive(), max_members=100, max_bytes=1_000_000)
        assert {path: value for path, value, _ in files}["result.txt"] == b"remote"
        container = binding.client.docker_client.containers.get(manager.session.state.container_id)
        assert container.attrs["HostConfig"]["NanoCpus"] == 500_000_000
        assert container.attrs["HostConfig"]["Memory"] == 128 * 1024 * 1024
        assert container.attrs["HostConfig"]["PidsLimit"] == 64
    finally:
        await manager.close()
