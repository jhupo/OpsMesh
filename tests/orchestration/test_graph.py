"""Authoring rejects unbounded graphs and mappings before any execution exists."""

import pytest

from opsmesh.orchestration.definitions.contracts import (
    FlowCondition,
    FlowDefinitionSpec,
    FlowGraph,
    FlowNode,
)
from opsmesh.orchestration.definitions.graph import validate_definition
from opsmesh.orchestration.runs.steps import compare, map_value
from opsmesh.shared.errors import InvalidArgumentError


def condition(key, **kwargs):
    return FlowNode(
        key=key, kind="condition", condition=FlowCondition(operator="eq", left=1, right=1), **kwargs
    )


@pytest.mark.parametrize(
    "nodes",
    [
        [condition("a", depends_on=["b"]), condition("b", depends_on=["a"])],
        [condition("a"), condition("a")],
        [condition("a", arguments={"x": {"$ref": "steps.b"}}), condition("b")],
        [FlowNode(key="wait", kind="wait", wait_kind="external", external_step="wait")],
    ],
)
def test_invalid_graph_has_no_execution(nodes):
    with pytest.raises(InvalidArgumentError):
        validate_definition(FlowDefinitionSpec(graph=FlowGraph(nodes=nodes)))


def test_loop_body_is_bounded_and_has_no_outer_step_access():
    from opsmesh.orchestration.definitions.contracts import FlowLimits

    body = FlowGraph(nodes=[condition("inner")])
    with pytest.raises(InvalidArgumentError):
        validate_definition(
            FlowDefinitionSpec(
                graph=FlowGraph(
                    nodes=[FlowNode(key="loop", kind="foreach", body=body, max_iterations=10)]
                ),
                limits=FlowLimits(max_steps=10),
            )
        )
    bad = FlowGraph(nodes=[condition("inner", arguments={"x": {"$ref": "steps.outer"}})])
    with pytest.raises(InvalidArgumentError):
        validate_definition(
            FlowDefinitionSpec(
                graph=FlowGraph(
                    nodes=[
                        condition("outer"),
                        FlowNode(key="loop", kind="repeat", body=bad, depends_on=["outer"]),
                    ]
                )
            )
        )


def test_mappings_and_conditions_have_finite_json_semantics():
    context = {"input": {"array": [3]}, "steps": {"answer": True}}
    assert map_value({"x": {"$ref": "input.array.0"}}, context) == {"x": 3}
    assert compare(FlowCondition(operator="eq", left=True, right=1), context) is False
    assert (
        compare(FlowCondition(operator="exists", left={"$ref": "input.missing"}), context) is False
    )
    with pytest.raises(ValueError):
        compare(FlowCondition(operator="lt", left=True, right=False), context)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.com/schema.json"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
        {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}},
        {"$dynamicRef": "#node"},
    ],
)
def test_schema_resolution_cannot_fetch_or_recurse(schema):
    from opsmesh.orchestration.definitions.schema_validation import validate_schema

    with pytest.raises(InvalidArgumentError):
        validate_schema(schema)
