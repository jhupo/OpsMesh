import asyncio
from uuid import uuid4

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolResult,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.factory import build_agent_runner
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings


def test_openai_agents_runner_builds_agent_from_profile() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={
            "temperature": 0.2,
            "max_tokens": 500,
            "verbosity": "low",
            "metadata": {"team": "research"},
            "unsupported": {"nested": True},
        },
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.name == "Researcher"
    assert agent.instructions == "Research carefully."
    assert agent.model == "gpt-4.1"
    assert agent.model_settings.temperature == 0.2
    assert agent.model_settings.max_tokens == 500
    assert agent.model_settings.verbosity == "low"
    assert agent.model_settings.metadata == {"team": "research"}
    assert not hasattr(agent.model_settings, "unsupported")


def test_openai_agents_runner_uses_request_provider_override() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="workspace-default",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        model="gpt-4.1-mini",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.model != profile.model
    assert agent.model.model == "gpt-4.1-mini"


def test_openai_agents_runner_registers_allowed_mcp_tools() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Create a poster",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("generate_image", "search.web"),
        ),
        tool_executor=RecordingToolExecutor(),
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert [tool.name for tool in agent.tools] == ["generate_image", "search.web"]


def test_openai_agents_runner_renders_tool_continuations_at_runtime_boundary() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Continue after the tool result.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        continuations=(
            AgentRuntimeToolContinuation(
                tool_name="generate_image",
                status="completed",
                result={"asset_id": "img_123"},
            ),
        ),
    )

    rendered = OpenAIAgentsRunner()._input_for_request(request)

    assert rendered.startswith("Continue after the tool result.")
    assert "Completed runtime tool results:" in rendered
    assert '"tool_name": "generate_image"' in rendered
    assert '"asset_id": "img_123"' in rendered


def test_fake_agent_runner_returns_deterministic_output() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Fake",
        role="worker",
        instructions="",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="hello",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
    )

    result = asyncio.run(FakeAgentRunner().run(request))

    assert result.final_output == "fake_run_completed"


def test_agent_runner_factory_respects_settings_backend() -> None:
    assert isinstance(
        build_agent_runner(Settings(environment="test", agent_runner_backend="fake")),
        FakeAgentRunner,
    )
    assert isinstance(
        build_agent_runner(Settings(environment="test", agent_runner_backend="openai")),
        OpenAIAgentsRunner,
    )


def test_openai_agents_runner_raw_output_is_json_safe() -> None:
    class Usage:
        def model_dump(self, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"requests": 1}

    class State:
        def to_json(self) -> str:
            return '{"schema_version":"1.10"}'

    class Result:
        final_output = "done"
        last_response_id = "resp_123"
        usage = Usage()

        class last_agent:
            name = "Researcher"

        def to_input_list(self, *, mode: str) -> list[dict[str, object]]:
            assert mode == "normalized"
            return [{"role": "assistant", "content": "done"}]

        def to_state(self) -> State:
            return State()

    payload = OpenAIAgentsRunner()._safe_raw_output(Result())

    assert payload == {
        "final_output": "done",
        "last_response_id": "resp_123",
        "last_agent": "Researcher",
        "resume_input": [{"role": "assistant", "content": "done"}],
        "run_state_json": '{"schema_version":"1.10"}',
        "usage": {"requests": 1},
    }


def test_agent_error_normalization_is_safe_for_persistence() -> None:
    error = normalize_agent_error(RuntimeError("network unavailable"))

    assert error.as_dict() == {
        "code": "RuntimeError",
        "message": "network unavailable",
        "retryable": True,
    }


class RecordingToolExecutor:
    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        return AgentRuntimeToolResult(status="completed", output={"ok": True})
