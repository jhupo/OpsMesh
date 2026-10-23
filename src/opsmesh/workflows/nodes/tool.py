"""Tool nodes use a caller-authorized invocation port."""

from opsmesh.workflows.contracts import ToolNode
from opsmesh.workflows.nodes.base import NodeRequest


async def execute(request: NodeRequest) -> object:
    assert isinstance(request.node, ToolNode)
    node = request.node
    binding = request.executor.context.prepared.bindings.tools[node.binding]
    return await request.executor.leaf(
        request.limits,
        lambda: request.executor.context.runtime.tool(
            request.path, binding, node.operation, request.arguments
        ),
    )
