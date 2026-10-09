import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeApprovalDecision,
    AgentRuntimeContext,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
)
from backend.app.agents.execution.providers.openai.runner import OpenAIAgentsRunner
from backend.app.agents.profiles.models import AgentProfile


def test_claude_uses_native_structured_output_without_text_fallback():
    import pytest
    from claude_agent_sdk import ResultMessage

    from backend.app.agents.execution.contracts import AgentRuntimeOutputSchema
    from backend.app.agents.execution.errors import AgentRuntimeOutputValidationError
    from backend.app.agents.execution.providers.claude.runner import ClaudeAgentSDKRunner

    profile = AgentProfile(id=uuid4(), workspace_id=uuid4(), name="Structured", role="expert")
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Answer",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id, task_id=None, run_id=uuid4()
        ),
        output_schema=AgentRuntimeOutputSchema(
            name="answer", version="1", schema={"type": "object"}
        ),
    )
    result = ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="offline",
        result='{"answer":"text"}',
        structured_output={"answer": "native"},
    )
    runner = ClaudeAgentSDKRunner()
    text, structured = runner._final_output(request, result, [])
    assert structured.value == {"answer": "native"}
    assert "native" in text
    with pytest.raises(AgentRuntimeOutputValidationError):
        runner._final_output(request, replace(result, structured_output=None), [])


def test_sdk_memory_requires_persistence_and_handles_empty_then_populated_storage(monkeypatch):
    import pytest
    from agents.sandbox.capabilities import Memory

    from backend.app.agents.execution.providers.openai.sandbox import (
        OpsMeshSandboxSession,
        sandbox_capabilities,
    )
    from backend.app.runtime.contracts import SandboxBinding, SandboxManifest, SandboxSession

    profile = AgentProfile(id=uuid4(), workspace_id=uuid4(), name="Memory", role="expert")
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        user_id=uuid4(),
        task_id=None,
        run_id=uuid4(),
        metadata={"sdk_memory": {"enabled": True, "read": True, "generate": False}},
    )
    executor = SimpleNamespace(read_file=lambda path: None)
    binding = SandboxBinding(
        manifest=SandboxManifest(run_id=context.run_id, root="/workspace"),
        session=SandboxSession(
            session_id="persistent", root="/workspace", backend="test", executor=executor
        ),
    )
    request = AgentRunRequest(
        agent_profile=profile, input_text="Remember", context=context, sandbox=binding
    )
    with pytest.raises(ValueError, match="persistent Runtime"):
        sandbox_capabilities(request)
    binding = replace(binding, session=replace(binding.session, persistent=True))
    request = replace(request, sandbox=binding)
    capabilities = sandbox_capabilities(request)
    assert [item.type for item in capabilities] == ["filesystem", "shell", "memory"]
    memory = next(item for item in capabilities if isinstance(item, Memory))
    assert memory.generate is None
    assert str(context.workspace_id) in memory.layout.memories_dir
    assert str(context.user_id) in memory.layout.memories_dir
    native = OpsMeshSandboxSession(binding)
    memory.session = native

    async def validated(path):
        return Path("/workspace") / path

    monkeypatch.setattr(native, "_check_read_with_exec", validated)

    async def read():
        assert await memory.instructions(native.state.manifest) is None
        executor.read_file = lambda path: b"Remember the confirmed incident procedure."
        prompt = await memory.instructions(native.state.manifest)
        assert "confirmed incident procedure" in prompt

    asyncio.run(read())


def test_native_mcp_approval_resume_preserves_call_identity_and_tool_output(monkeypatch, tmp_path):
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
        async def review_tool_call(self, **kwargs):
            return {"decision": "require_approval", "risk_level": "high"}

        async def execute_tool(self, *, tool_call_id, **kwargs):
            calls.append(tool_call_id)
            return AgentRuntimeToolResult(
                status="completed",
                output={
                    "content": [{"type": "text", "text": "original-runtime-result"}],
                    "isError": False,
                },
            )

    monkeypatch.setattr(
        "backend.app.agents.execution.providers.openai.runner.OpenAIProvider.get_model",
        lambda *args, **kwargs: OfflineModel(),
    )
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
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("inspect_logs",),
            tool_definitions=(
                AgentRuntimeToolDefinition(
                    name="inspect_logs",
                    source="mcp",
                    description="Inspect runtime logs",
                    input_schema={"type": "object", "properties": {"order": {"type": "string"}}},
                ),
            ),
        ),
        tool_executor=Executor(),
        session=storage,
    )

    async def execute():
        runner = OpenAIAgentsRunner()
        interrupted = await runner.run(request)
        assert calls == []
        assert interrupted.interruptions[0].tool_kind == "mcp"
        assert interrupted.interruptions[0].policy_decision["risk_level"] == "high"
        resumed = await runner.run(
            replace(
                request,
                resume_state=interrupted.resume_state,
                approval_decisions=(
                    AgentRuntimeApprovalDecision(
                        tool_call_id="original-call", tool_name="inspect_logs", status="approved"
                    ),
                ),
            )
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
