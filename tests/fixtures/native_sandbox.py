"""SDK public boundary double for product policy tests, not Docker certification."""

import io
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from agents.sandbox import Manifest
from agents.sandbox.sandboxes.docker import DockerSandboxClientOptions, DockerSandboxSessionState
from agents.sandbox.session import BaseSandboxClient, BaseSandboxSession
from agents.sandbox.snapshot import NoopSnapshot

from opsmesh.runtime.sandboxes.registry import SandboxBackend, SandboxBackendRegistry


def native_boundary(manifest=None):
    native = AsyncMock(spec=BaseSandboxSession)
    native.state = DockerSandboxSessionState(
        image="python:3.13",
        container_id="native-fixture",
        manifest=manifest or Manifest(root="/workspace"),
        snapshot=NoopSnapshot(id=str(uuid4())),
    )
    native.read.side_effect = lambda path: io.BytesIO(b"file")
    client = Mock(spec=BaseSandboxClient)
    client.backend_id = "docker"
    client.create = AsyncMock(return_value=native)
    client.resume = AsyncMock(return_value=native)
    client.delete = AsyncMock()
    client.serialize_session_state.side_effect = lambda state: state.model_dump(mode="json")
    client.deserialize_session_state.side_effect = DockerSandboxSessionState.model_validate
    registry = SandboxBackendRegistry(
        {"docker": SandboxBackend(client, DockerSandboxClientOptions.model_validate)}
    )
    return native, client, registry
