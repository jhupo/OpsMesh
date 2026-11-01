import pytest
from agents import CodeInterpreterTool, FileSearchTool, ImageGenerationTool, WebSearchTool
from pydantic import ValidationError

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentSpecification,
    CodeInterpreterBinding,
    FileSearchBinding,
    ImageGenerationBinding,
    WebSearchBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.backends.openai_agents.hosted_tools import build_hosted_tools


@pytest.mark.asyncio
async def test_hosted_tools_require_admission_and_map_actual_sdk_types():
    async def invoke(*args):
        raise AssertionError("Hosted tools do not invoke a local function")

    context = AgentExecutionContext("run", "session", "principal", invoke)
    spec = AgentSpecification(
        id="a",
        name="A",
        hosted_tools=(
            WebSearchBinding(allowed_domains=("example.com",)),
            FileSearchBinding(vector_store_ids=("vs_owned",)),
            CodeInterpreterBinding(file_ids=("file_owned",)),
            ImageGenerationBinding(quality="low"),
        ),
    )
    with pytest.raises(OpsMeshError) as error:
        await build_hosted_tools(spec, context)
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    admitted = []
    events = []
    context.services["authorize_hosted_tool"] = admitted.append

    async def emit(event):
        events.append(event)

    context.services["backend_event_sink"] = emit
    tools = await build_hosted_tools(spec, context)
    assert [type(tool) for tool in tools] == [
        WebSearchTool,
        FileSearchTool,
        CodeInterpreterTool,
        ImageGenerationTool,
    ]
    assert tools[0].filters.model_dump() == {"allowed_domains": ["example.com"]}
    assert tools[1].vector_store_ids == ["vs_owned"]
    assert tools[2].tool_config["container"]["file_ids"] == ["file_owned"]
    assert len(admitted) == 4 and events[0].payload["local_gateway_per_call"] is False


def test_hosted_binding_rejects_unknown_user_fields():
    with pytest.raises(ValidationError):
        AgentSpecification(id="a", name="A", hosted_tools=[{"type": "web_search", "api_key": "no"}])
