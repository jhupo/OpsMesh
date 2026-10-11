"""Physical host fixtures; no production model constructor compatibility."""

from uuid import UUID, uuid4

from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity
from opsmesh.runtime.instances.models import RuntimeHost


def execution_identity() -> RuntimeExecutionIdentity:
    return RuntimeExecutionIdentity(uuid4(), 100000, {"mode": "none"})


def runtime_host(
    workspace_id: UUID,
    container_id: str | None,
    *,
    capacity: int = 16,
    node_id: str = "test-node",
) -> RuntimeHost:
    return RuntimeHost(
        workspace_id=workspace_id,
        node_id=node_id,
        host_key=uuid4().hex,
        image="sha256:" + "0" * 64,
        docker_container_id=container_id,
        capacity=capacity,
        status="running",
    )
