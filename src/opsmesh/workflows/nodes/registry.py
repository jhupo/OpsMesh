"""Explicit node handlers; no dynamic plugin discovery inside graph execution."""

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.nodes import agent, control, tool
from opsmesh.workflows.nodes.base import NodeHandler


class NodeRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, NodeHandler] = {}

    def register(self, kind: str, handler: NodeHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"Node handler already registered: {kind}")
        self._handlers[kind] = handler

    def get(self, kind: str) -> NodeHandler:
        try:
            return self._handlers[kind]
        except KeyError:
            raise InvalidArgumentError(f"Unsupported workflow node: {kind}") from None


def builtin_nodes() -> NodeRegistry:
    result = NodeRegistry()
    result.register("agent", agent.execute)
    result.register("tool", tool.execute)
    for kind in (
        "transform",
        "condition",
        "join",
        "parallel",
        "foreach",
        "repeat",
        "subworkflow",
        "wait",
    ):
        result.register(kind, control.execute)
    return result
