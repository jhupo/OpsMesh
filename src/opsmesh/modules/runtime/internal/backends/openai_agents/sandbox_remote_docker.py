"""Mutually authenticated remote Docker client with per-session secret cleanup."""

import asyncio
import contextlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from opsmesh.framework.agents import AgentExecutionContext, SandboxConfiguration
from opsmesh.framework.agents.resources import SandboxProviderBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract

from .sandbox_docker import docker_client


class RemoteDockerOptions(OpsMeshContract):
    endpoint: str
    ca_secret_ref: str = Field(min_length=1)
    client_cert_secret_ref: str = Field(min_length=1)
    client_key_secret_ref: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    docker_options: dict[str, Any]

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("Remote Docker requires an HTTPS origin with mutual TLS")
        return self


async def create_remote_docker_provider(
    configuration: SandboxConfiguration,
    context: AgentExecutionContext,
    *,
    allowed_endpoints: tuple[str, ...],
) -> SandboxProviderBinding:
    try:
        options = RemoteDockerOptions.model_validate(configuration.options)
    except ValueError as error:
        raise OpsMeshError("CONFIG_INVALID", "Remote Docker configuration is invalid") from error
    if options.endpoint.rstrip("/") not in {item.rstrip("/") for item in allowed_endpoints}:
        raise OpsMeshError(
            "AUTHORIZATION_DENIED", "Docker endpoint is not in the deployment allowlist"
        )
    resolver = context.services.resolve_secret
    if resolver is None:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE", "Remote Docker credential resolver is unavailable"
        )
    try:
        from docker import DockerClient  # type: ignore[import-untyped]
        from docker.tls import TLSConfig  # type: ignore[import-untyped]
    except ImportError as error:
        raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Install the SDK docker extra") from error
    temporary = TemporaryDirectory(prefix="opsmesh-docker-tls-")
    native_client: Any = None
    closed = False

    async def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            if native_client is not None:
                await asyncio.to_thread(native_client.close)
        finally:
            temporary.cleanup()

    try:
        paths = {}
        for name, reference in (
            ("ca", options.ca_secret_ref),
            ("cert", options.client_cert_secret_ref),
            ("key", options.client_key_secret_ref),
        ):
            value = await resolver(reference)
            if not isinstance(value, str) or not value.strip():
                raise OpsMeshError("CONFIG_INVALID", "Docker TLS credential is empty")
            path = Path(temporary.name) / f"{name}.pem"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
            paths[name] = str(path)
        tls = TLSConfig(client_cert=(paths["cert"], paths["key"]), ca_cert=paths["ca"], verify=True)
        connection = asyncio.create_task(
            asyncio.to_thread(
                DockerClient, base_url=options.endpoint, tls=tls, timeout=options.timeout_seconds
            )
        )
        try:
            native_client = await asyncio.shield(connection)
        except asyncio.CancelledError:
            # A thread cannot be cancelled; retain ownership until its client can be closed.
            while not connection.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(connection)
            if not connection.cancelled() and connection.exception() is None:
                native_client = connection.result()
            raise
        client, native_options = docker_client(options.docker_options, client=native_client)
        return SandboxProviderBinding(client=client, options=native_options, aclose=close)
    except BaseException as error:
        await close()
        if isinstance(error, OpsMeshError | asyncio.CancelledError):
            raise
        raise OpsMeshError(
            "SANDBOX_CONNECTION_FAILED", "Remote Docker connection could not be initialized"
        ) from error
