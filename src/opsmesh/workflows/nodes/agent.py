"""Agent nodes delegate to execution-owned persistent instances and run records."""

from opsmesh.workflows.contracts import AgentNode
from opsmesh.workflows.nodes.base import NodeRequest


async def execute(request: NodeRequest) -> object:
    assert isinstance(request.node, AgentNode)
    binding = request.executor.context.prepared.bindings.agents[request.node.binding]
    return await request.executor.leaf(
        request.limits,
        lambda: request.executor.context.runtime.agent(request.path, binding, request.arguments),
    )
