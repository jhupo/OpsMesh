from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from agents.handoffs import HandoffInputData

import backend.app.agent_runtime.openai_agents as openai_runtime
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentRef,
    AgentRuntimeContext,
    AgentRuntimeHandoff,
)
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.agent_runtime.openai_results import OpenAIAgentsResultMapper
from backend.app.agents.models import AgentProfile


def _request(
    *,
    workspace_id=None,
    handoffs=(),
    handoff_agents=(),
) -> AgentRunRequest:
    workspace_id = workspace_id or uuid4()
    profile = AgentProfile(
        workspace_id=workspace_id,
        name="triage",
        role="manager",
        instructions="Route the request.",
        model="gpt-4.1-mini",
        model_settings={},
    )
    return AgentRunRequest(
        agent_profile=profile,
        input_text="route this",
        context=AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        api_key="sk-test",
        base_url="https://llm.example.test/v1",
        handoffs=handoffs,
        handoff_agents=handoff_agents,
    )


def test_openai_runner_builds_only_authorized_same_workspace_handoffs() -> None:
    workspace_id = uuid4()
    target_id = uuid4()
    request = _request(
        workspace_id=workspace_id,
        handoffs=(
            AgentRuntimeHandoff(
                target=AgentRuntimeAgentRef(name="reviewer", profile_id=target_id),
                reason="Review the result.",
                input_filter=("message_output_item",),
            ),
        ),
        handoff_agents=(
            AgentRuntimeAgentDefinition(
                ref=AgentRuntimeAgentRef(name="reviewer", profile_id=target_id),
                workspace_id=workspace_id,
                instructions="Review the result.",
                model="gpt-4.1-mini",
            ),
        ),
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert len(agent.handoffs) == 1
    handoff = agent.handoffs[0]
    assert handoff.agent_name == "reviewer"
    assert handoff.input_filter is not None

    items = (
        type("Message", (), {"type": "message_output_item"})(),
        type("Tool", (), {"type": "tool_call_item"})(),
    )
    filtered = handoff.input_filter(
        HandoffInputData(
            input_history="route this",
            pre_handoff_items=(),
            new_items=items,
        )
    )

    assert len(filtered.input_items or ()) == 1
    assert filtered.input_items[0].type == "message_output_item"


def test_openai_runner_rejects_missing_or_cross_workspace_handoff_target() -> None:
    workspace_id = uuid4()
    target_id = uuid4()
    descriptor = AgentRuntimeHandoff(
        target=AgentRuntimeAgentRef(name="reviewer", profile_id=target_id)
    )
    missing_target = _request(workspace_id=workspace_id, handoffs=(descriptor,))
    with pytest.raises(ValueError, match="not in the authorized target set"):
        OpenAIAgentsRunner()._build_agent(missing_target)

    foreign_target = _request(
        workspace_id=workspace_id,
        handoffs=(descriptor,),
        handoff_agents=(
            AgentRuntimeAgentDefinition(
                ref=AgentRuntimeAgentRef(name="reviewer", profile_id=target_id),
                workspace_id=uuid4(),
                instructions="Foreign.",
            ),
        ),
    )
    with pytest.raises(ValueError, match="another workspace"):
        OpenAIAgentsRunner()._build_agent(foreign_target)


def test_openai_result_mapper_records_completed_handoff_and_filter_audit() -> None:
    source = type("Source", (), {"name": "triage"})()
    target = type("Target", (), {"name": "reviewer"})()
    item = type(
        "HandoffOutput",
        (),
        {
            "type": "handoff_output_item",
            "source_agent": source,
            "target_agent": target,
        },
    )()
    result = type("Result", (), {"new_items": [item]})()

    mapped = OpenAIAgentsResultMapper().handoffs(
        result,
        {"reviewer": {"filtered_context_keys": ("tool_call_item",), "input_items_after": 1}},
    )

    assert len(mapped) == 1
    assert mapped[0].source.name == "triage"
    assert mapped[0].target.name == "reviewer"
    assert mapped[0].status == "completed"
    assert mapped[0].filtered_context_keys == ("tool_call_item",)
    assert mapped[0].metadata == {"input_items_after": 1}


def test_openai_runner_returns_handoff_result_and_durable_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    request = _request(
        workspace_id=workspace_id,
        handoffs=(
            AgentRuntimeHandoff(
                target=AgentRuntimeAgentRef(name="reviewer"),
                input_filter=("message_output_item",),
            ),
        ),
        handoff_agents=(
            AgentRuntimeAgentDefinition(
                ref=AgentRuntimeAgentRef(name="reviewer"),
                workspace_id=workspace_id,
                instructions="Review the result.",
            ),
        ),
    )
    source = type("Source", (), {"name": "triage"})()
    target = type("Target", (), {"name": "reviewer"})()

    async def fake_runner_run(agent: object, *_: object, **__: object) -> object:
        handoff = agent.handoffs[0]
        handoff.input_filter(
            HandoffInputData(
                input_history="route this",
                pre_handoff_items=(),
                new_items=(type("Message", (), {"type": "message_output_item"})(),),
            )
        )
        item = type(
            "HandoffOutput",
            (),
            {
                "type": "handoff_output_item",
                "source_agent": source,
                "target_agent": target,
            },
        )()
        return type("Result", (), {"final_output": "reviewed", "new_items": [item]})()

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    result = asyncio.run(OpenAIAgentsRunner().run(request))

    assert result.handoffs[0].target.name == "reviewer"
    assert result.handoffs[0].filtered_context_keys == ()
    assert any(event.event_type == "agent.handoff" for event in result.events)
