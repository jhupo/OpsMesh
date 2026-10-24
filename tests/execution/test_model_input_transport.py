"""Custom tool content survives the native Temporal JSON payload converter."""

from typing import Any, cast

import pytest
from agents import Agent
from agents.items import TResponseInputItem
from agents.run_config import CallModelData, ModelInputData
from pydantic import TypeAdapter
from temporalio.openai_agents import OpenAIPayloadConverter

from opsmesh.execution.runner import structured_tool_outputs


@pytest.mark.parametrize("text", ["Created acceptance/main.py\n", "", "42"])
def test_custom_tool_text_roundtrips_through_native_converter(text):
    original: TResponseInputItem = {
        "type": "custom_tool_call_output",
        "call_id": "patch-1",
        "output": text,
    }
    message: TResponseInputItem = {"role": "user", "content": "Write and test code"}
    data = ModelInputData(input=[message, original], instructions="Use the sandbox")
    result = structured_tool_outputs(CallModelData(data, Agent(name="Coder"), None))
    expected = {
        "type": "custom_tool_call_output",
        "call_id": "patch-1",
        "output": [{"type": "input_text", "text": text}],
    }
    assert original["output"] == text
    assert result.input[0] is message
    assert result.instructions == data.instructions
    converter = OpenAIPayloadConverter()
    payloads = converter.to_payloads([result.input])
    restored = converter.from_payloads(payloads, [list[TResponseInputItem]])[0]
    assert TypeAdapter(Any).dump_python(restored, mode="json") == [message, expected]


def test_structured_custom_tool_content_is_preserved():
    output = cast(
        "TResponseInputItem",
        {
            "type": "custom_tool_call_output",
            "call_id": "read-1",
            "output": [
                {"type": "input_text", "text": "Screenshot"},
                {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
            ],
        },
    )
    result = structured_tool_outputs(
        CallModelData(ModelInputData(input=[output], instructions=None), Agent(name="Coder"), None)
    )
    assert result.input == [output]
    assert result.input[0] is output
