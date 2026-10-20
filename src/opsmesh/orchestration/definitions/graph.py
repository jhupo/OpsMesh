"""DAG, mapping and resource bounds; no Task or planning strategy dependencies."""

import json
from graphlib import CycleError, TopologicalSorter
from typing import Any

from jsonschema import Draft202012Validator

from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec, FlowGraph
from opsmesh.orchestration.definitions.schema_validation import validate_schema
from opsmesh.shared.errors import InvalidArgumentError


def mapping_references(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 20:
        raise InvalidArgumentError("The flow mapping exceeds its nesting bound")
    references: set[str] = set()
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise InvalidArgumentError("A mapping reference must be a single bounded path")
            path = value["$ref"].split(".")
            if len(path) > 20 or any(not part or part.startswith("_") for part in path):
                raise InvalidArgumentError("Invalid flow mapping path")
            if path[0] not in {"input", "steps", "item", "index"}:
                raise InvalidArgumentError("The flow mapping source is not allowed")
            if path[0] == "steps":
                if len(path) < 2:
                    raise InvalidArgumentError("A step mapping must name its source")
                references.add(path[1])
        else:
            for item in value.values():
                references |= mapping_references(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            references |= mapping_references(item, depth=depth + 1)
    return references


def validate_graph(graph: FlowGraph, *, depth: int = 0) -> int:
    if depth > 8:
        raise InvalidArgumentError("The nested flow graph exceeds depth 8")
    nodes = {node.key: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise InvalidArgumentError("Flow node keys must be unique")
    dependencies = {key: set(node.depends_on) for key, node in nodes.items()}
    if any(not related <= nodes.keys() for related in dependencies.values()):
        raise InvalidArgumentError("A flow dependency does not exist")
    try:
        ordered = list(TopologicalSorter(dependencies).static_order())
    except CycleError:
        raise InvalidArgumentError("The flow graph contains a cycle") from None
    ancestors: dict[str, set[str]] = {}
    total = 0
    for key in ordered:
        node = nodes[key]
        ancestors[key] = set(node.depends_on)
        for parent in node.depends_on:
            ancestors[key] |= ancestors[parent]
        if (
            node.kind == "wait"
            and node.wait_kind == "external"
            and node.external_step not in ancestors[key]
        ):
            raise InvalidArgumentError(
                "An external wait must reference its completed owned ancestor step"
            )
        values = [node.arguments]
        if node.condition:
            values.append(node.condition.model_dump())
        if node.run_if:
            values.append(node.run_if.model_dump())
        references = set().union(*(mapping_references(value) for value in values))
        if not references <= ancestors[key]:
            raise InvalidArgumentError(
                "A flow mapping reads a step outside its completed dependencies"
            )
        for schema in (node.input_schema, node.output_schema):
            validate_schema(schema)
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                raise InvalidArgumentError("A flow node schema is invalid") from None
        total += 1
        if node.body:
            multiplier = node.max_iterations if node.kind in {"foreach", "repeat"} else 1
            total += multiplier * validate_graph(node.body, depth=depth + 1)
    if not mapping_references(graph.output) <= nodes.keys():
        raise InvalidArgumentError("The graph output references a missing step")
    return total


def validate_definition(spec: FlowDefinitionSpec) -> None:
    if len(json.dumps(spec.model_dump(mode="json")).encode()) > 262144:
        raise InvalidArgumentError("The published flow exceeds its byte bound")
    if validate_graph(spec.graph) > spec.limits.max_steps:
        raise InvalidArgumentError("The graph exceeds its shared step limit")
    for schema in (spec.input_schema, spec.output_schema):
        validate_schema(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            raise InvalidArgumentError("A flow input/output schema is invalid") from None
