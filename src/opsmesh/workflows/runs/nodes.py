"""Resolve a bounded invocation path back to its frozen graph node."""

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.contracts import (
    ForeachNode,
    ParallelNode,
    PreparedWorkflow,
    RepeatNode,
    SubworkflowNode,
    WorkflowNode,
)


def resolve_node(prepared: PreparedWorkflow, path: str) -> WorkflowNode:
    parts = path.split("/")
    if len(path) > 2000 or not parts or parts.pop(0) != "entry":
        raise InvalidArgumentError("Invalid workflow node path")
    graph = prepared.plan.entry.spec.graph
    while parts:
        key = parts.pop(0)
        node = next((item for item in graph.nodes if item.key == key), None)
        if node is None:
            raise InvalidArgumentError("The node is not in this workflow")
        if not parts:
            return node
        if isinstance(node, SubworkflowNode):
            graph = prepared.plan.workflows[node.revision_id].spec.graph
            continue
        label = parts.pop(0)
        if isinstance(node, ParallelNode):
            prefix, bound = "branch_", len(node.branches)
        elif isinstance(node, ForeachNode):
            prefix, bound = "item_", node.max_items
        elif isinstance(node, RepeatNode):
            prefix, bound = "iteration_", node.max_iterations
        else:
            raise InvalidArgumentError("This node does not contain child nodes")
        number = label.removeprefix(prefix)
        if (
            not label.startswith(prefix)
            or not number.isascii()
            or not number.isdecimal()
            or not 0 <= int(number) < bound
        ):
            raise InvalidArgumentError("The child node occurrence exceeds its bounds")
        graph = node.branches[int(number)] if isinstance(node, ParallelNode) else node.body
    raise InvalidArgumentError("A workflow path must select a node")
