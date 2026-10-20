"""Pure materialization rejects malformed and amplified inputs before external effects."""

import math

import pytest

from opsmesh_sdk.composition.parameters import ParameterLimits, normalize, render
from opsmesh_sdk.errors import ValidationError


def test_complete_parameters_preserve_types_without_aliasing() -> None:
    arguments = {"count": 3, "options": {"sorted": True}, "label": "${count}"}
    result = render(["${count}", "${options}", "${label}", "limit=${count}"], arguments)
    assert result == [3, {"sorted": True}, "${count}", "limit=3"]
    arguments["options"]["sorted"] = False
    assert result[1] == {"sorted": True}


def test_substitution_does_not_rewrite_keys_or_execute_expressions() -> None:
    assert render({"${name}": "${name}"}, {"name": "foo"}) == {"${name}": "foo"}
    assert render("${__import__('os')}", {}) == "${__import__('os')}"
    with pytest.raises(ValidationError):
        render("${missing}", {})
    with pytest.raises(ValidationError):
        render("options=${options}", {"options": [1]})


@pytest.mark.parametrize("value", [math.nan, math.inf, {1: "x"}, {"x": object()}, (1, 2)])
def test_non_json_values_are_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        normalize(value)


def test_cyclic_and_deep_inputs_are_rejected() -> None:
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValidationError):
        render(cyclic, {})
    deep = [[[[["leaf"]]]]]
    with pytest.raises(ValidationError):
        render(deep, {}, limits=ParameterLimits(max_depth=3))
    with pytest.raises(ValidationError):
        normalize([1, 2, 3], limits=ParameterLimits(max_nodes=3))


def test_rendered_output_enforces_combined_depth_nodes_and_bytes() -> None:
    with pytest.raises(ValidationError):
        render(["${x}"] * 20, {"x": "a" * 100}, limits=ParameterLimits(max_bytes=500))
    with pytest.raises(ValidationError):
        render(["${x}"] * 10, {"x": [1, 2, 3]}, limits=ParameterLimits(max_nodes=15))
    with pytest.raises(ValidationError):
        render([["${x}"]], {"x": [[1]]}, limits=ParameterLimits(max_depth=3))
    with pytest.raises(ValidationError):
        render("${x}${x}${x}${x}", {"x": "a" * 50}, limits=ParameterLimits(max_bytes=100))


def test_normalization_orders_keys_and_bounds_encoded_unicode() -> None:
    assert list(normalize({"z": 1, "a": 2})) == ["a", "z"]
    with pytest.raises(ValidationError):
        normalize("\u4e2d" * 10, limits=ParameterLimits(max_bytes=30))
