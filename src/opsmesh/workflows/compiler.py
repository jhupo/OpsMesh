"""Compile bounded graphs and immutable references without persistence or I/O."""

from collections.abc import Callable
from uuid import UUID

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.serialization import payload_digest
from opsmesh.workflows.contracts import (
    AgentNode,
    CompiledWorkflow,
    GraphBounds,
    SubworkflowNode,
    ToolNode,
    WorkflowGraph,
    WorkflowPlan,
    WorkflowSpec,
)
from opsmesh.workflows.graph import nested_graphs, ordered_nodes, validate_graph


def compile_spec(
    spec: WorkflowSpec,
    *,
    workflow_id: UUID,
    revision_id: UUID | None = None,
    load: Callable[[UUID], CompiledWorkflow],
) -> WorkflowPlan:
    workflows: dict[UUID, CompiledWorkflow] = {}
    agents: set[str] = set()
    tools: set[str] = set()

    def graph(
        current: WorkflowGraph, owner: WorkflowSpec, path: frozenset[UUID], depth: int
    ) -> GraphBounds:
        if depth > spec.limits.max_depth:
            raise InvalidArgumentError("The workflow closure exceeds max_depth")
        validate_graph(current, owner.limits)
        steps, height = 0, 1
        for node in ordered_nodes(current):
            steps += 1
            if isinstance(node, AgentNode):
                agents.add(node.binding)
            elif isinstance(node, ToolNode):
                tools.add(node.binding)
            elif isinstance(node, SubworkflowNode):
                child = load(node.revision_id)
                if child.workflow_id in path:
                    raise InvalidArgumentError("Workflow references must not form a cycle")
                if (
                    child.revision_id != node.revision_id
                    or payload_digest(child.spec.model_dump(mode="json")) != child.digest
                ):
                    raise InvalidArgumentError("The referenced revision identity or digest changed")
                if node.revision_id not in workflows and len(workflows) >= 256:
                    raise InvalidArgumentError("Too many referenced workflow revisions")
                bounds = graph(child.spec.graph, child.spec, path | {child.workflow_id}, depth + 1)
                workflows[node.revision_id] = child.model_copy(update={"bounds": bounds})
                steps += bounds.steps
                height = max(height, bounds.depth + 1)
            for _, body, multiplier in nested_graphs(node):
                bounds = graph(body, owner, path, depth + 1)
                steps += bounds.steps * multiplier
                height = max(height, bounds.depth + 1)
            if steps > min(spec.limits.max_steps, owner.limits.max_steps):
                raise InvalidArgumentError("The workflow closure exceeds max_steps")
        if height > owner.limits.max_depth:
            raise InvalidArgumentError("A referenced graph exceeds its own max_depth")
        return GraphBounds(steps=steps, depth=height)

    bounds = graph(spec.graph, spec, frozenset({workflow_id}), 1)
    if agents & tools:
        raise InvalidArgumentError("Agent and tool slots must have distinct names")
    return WorkflowPlan(
        entry=CompiledWorkflow(
            workflow_id=workflow_id,
            revision_id=revision_id,
            digest=payload_digest(spec.model_dump(mode="json")),
            spec=spec,
            bounds=bounds,
        ),
        workflows=workflows,
        agent_slots=sorted(agents),
        tool_slots=sorted(tools),
    )
