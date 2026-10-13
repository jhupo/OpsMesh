import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentRunRequest,
    AgentToolApprovalDecision,
    AgentToolSpec,
    ToolExecutionResult,
)
from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor
from opsmesh.agents.profiles.models import AgentProfile


def test_native_product_tool_approval_resume_preserves_call_identity_and_tool_output(
    monkeypatch, tmp_path
):
    from agents.extensions.memory import SQLAlchemySession
    from sqlalchemy.ext.asyncio import create_async_engine

    seen = []
    calls = []

    class OfflineModel(Model):
        async def get_response(self, *, input, **kwargs):
            seen.append(input)
            if len(seen) == 1:
                output = [
                    ResponseFunctionToolCall(
                        type="function_call",
                        name="inspect_logs",
                        call_id="original-call",
                        arguments='{"order":"offline"}',
                    )
                ]
            else:
                output = [
                    ResponseOutputMessage(
                        id="answer",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(type="output_text", text="analyzed", annotations=[])
                        ],
                    )
                ]
            return ModelResponse(
                output=output,
                usage=Usage(requests=1, input_tokens=7, output_tokens=3, total_tokens=10),
                response_id="offline",
            )

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("No streaming in this offline test")
            yield

    class Executor:
        async def authorize_agent(self, **kwargs):
            pass

        async def review_tool_call(self, **kwargs):
            return {"decision": "require_approval", "risk_level": "high"}

        async def execute_tool(self, *, tool_call_id, **kwargs):
            calls.append(tool_call_id)
            return ToolExecutionResult(
                status="completed",
                output={
                    "content": [{"type": "text", "text": "original-runtime-result"}],
                    "isError": False,
                },
            )

    from agents import ModelProvider

    from tests.fixtures.sdk import install_model_provider

    class Provider(ModelProvider):
        def get_model(self, name):
            return OfflineModel()

    install_model_provider(monkeypatch, Provider())
    profile = AgentProfile(
        id=uuid4(),
        workspace_id=uuid4(),
        name="Analyst",
        role="expert",
        instructions="Analyze logs",
        model="gpt-5",
        model_settings={},
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    storage = SQLAlchemySession("native-approval", engine=engine, create_tables=True)
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Analyze this order",
        api_key="offline-key",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("inspect_logs",),
            tool_definitions=(
                AgentToolSpec(
                    name="inspect_logs",
                    source="product",
                    description="Inspect runtime logs",
                    input_schema={"type": "object", "properties": {"order": {"type": "string"}}},
                ),
            ),
        ),
        tool_executor=Executor(),
        model_provider_credential_id=uuid4(),
    )

    async def execute():
        runner = OpenAIAgentExecutor()
        interrupted = await runner.run(request, session=storage)
        assert calls == []
        assert interrupted.interruptions[0].tool_kind == "product"
        assert interrupted.interruptions[0].policy_decision["risk_level"] == "high"
        resumed = await runner.run(
            replace(
                request,
                resume_state=interrupted.resume_state,
                approval_decisions=(
                    AgentToolApprovalDecision(
                        tool_call_id="original-call", tool_name="inspect_logs", status="approved"
                    ),
                ),
            ),
            session=storage,
        )
        assert resumed.final_output == "analyzed"
        assert resumed.usage is not None
        assert resumed.usage.total_tokens >= 10
        assert resumed.raw_output["usage"]["total_tokens"] == resumed.usage.total_tokens
        history = await storage.get_items()
        assert len([item for item in history if item.get("role") == "user"]) == 1
        assert len([item for item in history if item.get("type") == "function_call_output"]) == 1
        await engine.dispose()

    asyncio.run(execute())
    assert calls == ["original-call"]
    assert any(
        item.get("type") == "function_call_output"
        and item.get("call_id") == "original-call"
        and "original-runtime-result" in str(item.get("output"))
        for item in seen[-1]
    )
    assert len([item for item in seen[-1] if item.get("role") == "user"]) == 1


@pytest.mark.parametrize("generate", [False, True])
def test_sdk_memory_requires_native_client_and_uses_tenant_layout(generate):
    from agents.sandbox.capabilities import Memory

    from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
    from opsmesh.agents.execution.providers.openai.sandbox import sandbox_capabilities

    profile = AgentProfile(
        id=uuid4(),
        workspace_id=uuid4(),
        name="Memory",
        role="expert",
        model="gpt-4.1",
        model_settings={},
    )
    context = AgentExecutionContext(
        workspace_id=profile.workspace_id,
        user_id=uuid4(),
        task_id=None,
        run_id=uuid4(),
        metadata={"sdk_memory": {"enabled": True, "read": True, "generate": generate}},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Remember",
        context=context,
        model_provider_credential_id=uuid4(),
    )
    with pytest.raises(ValueError, match="native Temporal sandbox client"):
        sandbox_capabilities(request)
    request = replace(request, sdk_sandbox_client=object())
    capabilities = sandbox_capabilities(request)
    memory = next(item for item in capabilities if isinstance(item, Memory))
    if generate:
        assert memory.generate is not None
        reference = AgentModelReference.decode(memory.generate.phase_one_model)
        assert reference.purpose == "memory"
        assert reference.credential_id == request.model_provider_credential_id
        assert memory.generate.phase_one_model == memory.generate.phase_two_model
    else:
        assert memory.generate is None
    assert str(context.workspace_id) in memory.layout.memories_dir
    assert str(context.user_id) in memory.layout.memories_dir
    assert str(profile.id) in memory.layout.memories_dir
