"""DAG validation, isolated mapping scopes and bounded fixed-node expansion."""

from graphlib import CycleError, TopologicalSorter

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.conditions import condition_values
from opsmesh.workflows.contracts import (
    ConditionNode,
    ForeachNode,
    GraphBounds,
    ParallelNode,
    RepeatNode,
    TransformNode,
    WorkflowGraph,
    WorkflowLimits,
    WorkflowNode,
)
from opsmesh.workflows.mappings import mapping_references
from opsmesh.workflows.schema import validate_parameter_schema


def ordered_nodes(graph: WorkflowGraph) -> list[WorkflowNode]:
    nodes = {node.key: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise InvalidArgumentError("Workflow node keys must be unique within each graph")
    dependencies = {node.key: node.depends_on for node in graph.nodes}
    for node in graph.nodes:
        if len(node.depends_on) != len(set(node.depends_on)):
            raise InvalidArgumentError("Workflow dependencies must be unique")
        if not set(dependencies[node.key]).issubset(nodes):
            raise InvalidArgumentError("A workflow dependency does not exist in its graph")
    try:
        return [nodes[key] for key in TopologicalSorter(dependencies).static_order()]
    except CycleError as exc:
        raise InvalidArgumentError("The workflow graph contains a cycle") from exc


def nested_graphs(node: WorkflowNode) -> list[tuple[str, WorkflowGraph, int]]:
    """Return static children with their maximum execution multiplicity."""
    if isinstance(node, ParallelNode):
        return [(f"branch_{index}", graph, 1) for index, graph in enumerate(node.branches)]
    if isinstance(node, ForeachNode):
        return [("body", node.body, node.max_items)]
    if isinstance(node, RepeatNode):
        return [("body", node.body, node.max_iterations)]
    return []


def _validate_reads(values: list[object], allowed: set[str]) -> None:
    references = set().union(*(mapping_references(value) for value in values))
    if not references.issubset(allowed):
        raise InvalidArgumentError("A mapping reads a step outside its completed dependencies")


def validate_graph(graph: WorkflowGraph, limits: WorkflowLimits, *, depth: int = 1) -> GraphBounds:
    if depth > limits.max_depth:
        raise InvalidArgumentError("The nested workflow exceeds its depth limit")
    ancestors: dict[str, set[str]] = {}
    steps, height = 0, 1
    for node in ordered_nodes(graph):
        ancestors[node.key] = set(node.depends_on)
        for parent in node.depends_on:
            ancestors[node.key].update(ancestors[parent])
        values: list[object] = [node.arguments]
        if node.when is not None:
            values.extend(condition_values(node.when))
        if isinstance(node, ConditionNode):
            values.extend(condition_values(node.condition))
        if isinstance(node, TransformNode):
            values.append(node.value)
        if isinstance(node, ForeachNode):
            values.append(node.items)
        _validate_reads(values, ancestors[node.key])
        for schema in (node.input_schema, node.output_schema):
            validate_parameter_schema(schema)
        if (
            isinstance(node, (ParallelNode, ForeachNode))
            and node.concurrency > limits.max_concurrency
        ):
            raise InvalidArgumentError("A composite concurrency exceeds the workflow limit")
        steps += 1
        for _, child, multiplier in nested_graphs(node):
            bounds = validate_graph(child, limits, depth=depth + 1)
            steps += bounds.steps * multiplier
            height = max(height, bounds.depth + 1)
        if isinstance(node, RepeatNode):
            _validate_reads(condition_values(node.until), {child.key for child in node.body.nodes})
        if steps > limits.max_steps:
            raise InvalidArgumentError("The workflow exceeds its worst-case step limit")
    _validate_reads([graph.output], {node.key for node in graph.nodes})
    return GraphBounds(steps=steps, depth=height)
