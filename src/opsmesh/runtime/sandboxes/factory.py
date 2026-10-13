from contextlib import ExitStack, closing

import docker
from agents.sandbox.sandboxes import DockerSandboxClient, DockerSandboxClientOptions
from agents.sandbox.session.dependencies import Dependencies
from docker.client import DockerClient
from docker.constants import DEFAULT_DOCKER_API_VERSION

from opsmesh.resources.storage.backends import create_storage
from opsmesh.runtime.sandboxes.registry import SandboxBackend, SandboxBackendRegistry
from opsmesh.runtime.sandboxes.snapshots import SNAPSHOT_DEPENDENCY, SandboxSnapshotStorage
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.shared.db.session import SessionLocal
from opsmesh.workspaces.projects.io.native import (
    PROJECT_FILES_DEPENDENCY,
    ProjectSandboxFileService,
)


def build_sandbox_backends(
    settings: Settings, client: DockerClient | None = None
) -> SandboxBackendRegistry:
    """Own storage and self-created Docker resources until registry shutdown."""
    resources = ExitStack()
    try:
        storage = resources.enter_context(closing(create_storage(settings)))
        dependencies = Dependencies.with_values(
            {
                SNAPSHOT_DEPENDENCY: SandboxSnapshotStorage(storage),
                PROJECT_FILES_DEPENDENCY: ProjectSandboxFileService(
                    ActivityDependencies(sessions=SessionLocal, settings=settings),
                    storage,
                ),
            }
        )
        # Select the supported API contract without a startup daemon request.
        # The native SDK contacts Docker only when sandbox work is actually admitted.
        docker_client = (
            client if client is not None else docker.from_env(version=DEFAULT_DOCKER_API_VERSION)
        )
        if client is None:
            resources.callback(docker_client.close)
        sdk_client = DockerSandboxClient(docker_client, dependencies=dependencies)
        return SandboxBackendRegistry(
            {
                "docker": SandboxBackend(
                    client=sdk_client,
                    parse_options=lambda options: DockerSandboxClientOptions.model_validate(
                        options, extra="forbid"
                    ),
                ),
            },
            close_resources=resources.close,
        )
    except BaseException:
        resources.close()
        raise
