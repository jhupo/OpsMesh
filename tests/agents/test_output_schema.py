"""JSON Schema output preserves validation and the native SDK schema contract."""

import pytest
from agents import AgentOutputSchema
from agents.exceptions import ModelBehaviorError

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.config.output import JsonSchemaOutput
from opsmesh.agents.resolvers.output import resolve_output


def test_json_schema_output_uses_native_concrete_schema_contract():
    output = resolve_output(
        JsonSchemaOutput(
            name="Answer",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        ),
        CallbackRegistry(),
    )
    assert isinstance(output, AgentOutputSchema)
    assert output.name() == "Answer"
    assert output.is_strict_json_schema()
    assert not output.is_plain_text()
    assert output.validate_json('{"answer":42}') == {"answer": 42}
    with pytest.raises(ModelBehaviorError):
        output.validate_json('{"answer":"wrong"}')
    schema = output.json_schema()
    schema.clear()
    assert output.json_schema()["type"] == "object"
