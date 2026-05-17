import asyncio
from uuid import uuid4

from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRuntimeContext
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.agents.models import AgentProfile


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


def test_agent_error_normalization_is_safe_for_persistence() -> None:
    error = normalize_agent_error(RuntimeError("network unavailable"))

    assert error.as_dict() == {
        "code": "RuntimeError",
        "message": "network unavailable",
        "retryable": True,
    }
