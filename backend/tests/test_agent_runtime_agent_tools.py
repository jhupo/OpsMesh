from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents.tool_context import ToolContext

import backend.app.agent_runtime.adapters.openai_agents as openai_runtime
from backend.app.agent_runtime.core.contracts import (
    AgentRunRequest,
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentRef,
    AgentRuntimeAgentTool,
    AgentRuntimeContext,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
)
from backend.app.agent_runtime.adapters.openai_agents import OpenAIAgentsRunner
from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings
from backend.app.orchestration.run_authorization_snapshot import (
    RunAuthorizationSnapshotService,
)
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.orchestration.run_result_payloads import run_output_payload
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.jobs import JobPayload, JobType
from backend.tests.test_worker_run_execution import _seed_workspace, _session


class RecordingExecutor:
    def __init__(self) -> None:
        self.contexts: list[AgentRuntimeContext] = []

    def review_tool_call(self, **_: object) -> dict[str, object]:
        return {"decision": "allow", "risk_level": "low", "reasons": []}

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str,
        approval_granted: bool,
    ) -> AgentRuntimeToolResult:
        self.contexts.append(context)
        return AgentRuntimeToolResult(
            status="completed",
            output={"tool_name": tool_name, "arguments": arguments},
        )


def test_openai_agent_tool_uses_sdk_and_scoped_runtime_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    manager_id = uuid4()
    specialist_id = uuid4()
    run_id = uuid4()
    root_context = AgentRuntimeContext(
        workspace_id=workspace_id,
        task_id=None,
        run_id=run_id,
        allowed_tools=("read_data", "write_data"),
        tool_definitions=(_tool("read_data"), _tool("write_data")),
    )
    specialist_context = AgentRuntimeContext(
        workspace_id=workspace_id,
        task_id=None,
        run_id=run_id,
        allowed_tools=("read_data",),
        tool_definitions=(_tool("read_data"),),
        metadata={"agent_tool": {"depth": 1, "max_depth": 1}},
    )
    manager = AgentProfile(
        id=manager_id,
        workspace_id=workspace_id,
        name="Manager",
        role="manager",
        instructions="Delegate research.",
        model="gpt-4.1-mini",
        model_settings={},
    )
    target = AgentRuntimeAgentDefinition(
        ref=AgentRuntimeAgentRef(
            name="Researcher",
            profile_id=specialist_id,
            role="researcher",
        ),
        workspace_id=workspace_id,
        instructions="Research only the delegated question.",
        model_settings={},
    )
    executor = RecordingExecutor()
    request = AgentRunRequest(
        agent_profile=manager,
        input_text="Delegate this research.",
        context=root_context,
        provider="openai",
        api_key="sk-test",
        tool_executor=executor,
        agent_tools=(
            AgentRuntimeAgentTool(
                target=target,
                tool_name="delegate_to_researcher",
                description="Delegate research.",
                context=specialist_context,
                max_turns=4,
                depth=1,
                max_depth=1,
                model="gpt-4.1-mini",
                provider="openai",
                api_key="sk-test",
            ),
        ),
    )

    async def fake_run(*args: object, **kwargs: object) -> object:
        agent = kwargs.get("starting_agent") or args[0]
        if getattr(agent, "name", None) == "Researcher":
            context = kwargs["context"]
            return SimpleNamespace(
                final_output="research complete",
                interruptions=[],
                usage={"input_tokens": 3, "output_tokens": 2},
                agent_tool_invocation=SimpleNamespace(
                    tool_call_id=context.tool_call_id,
                ),
                new_items=[],
            )
        agent_tool = next(item for item in agent.tools if item.name == "delegate_to_researcher")
        output = await agent_tool.on_invoke_tool(
            ToolContext(
                context=kwargs["context"],
                tool_name=agent_tool.name,
                tool_call_id="agent-call-1",
                tool_arguments='{"input":"research this"}',
                agent=agent,
                run_config=kwargs.get("run_config"),
            ),
            '{"input":"research this"}',
        )
        assert output == "research complete"
        return SimpleNamespace(
            final_output="manager complete",
            new_items=[],
            events=[],
            usage=None,
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)
    result = asyncio.run(OpenAIAgentsRunner().run(request))
    built = OpenAIAgentsRunner()._build_agent(request)
    sdk_agent_tool = next(item for item in built.tools if item.name == "delegate_to_researcher")
    specialist = sdk_agent_tool._agent_instance
    specialist_product_tool = next(item for item in specialist.tools if item.name == "read_data")
    asyncio.run(
        specialist_product_tool.on_invoke_tool(
            SimpleNamespace(context=root_context, tool_call_id="read-call-1"),
            '{"query":"scoped"}',
        )
    )

    assert sdk_agent_tool._is_agent_tool is True
    assert [item.name for item in specialist.tools] == ["read_data"]
    assert executor.contexts == [specialist_context]
    assert result.agent_tool_calls[0].tool_call_id == "agent-call-1"
    assert result.agent_tool_calls[0].source.profile_id == manager_id
    assert result.agent_tool_calls[0].target.profile_id == specialist_id
    assert result.agent_tool_calls[0].usage == {"input_tokens": 3, "output_tokens": 2}
    assert any(item.event_type == "agent.tool.completed" for item in result.events)
    persisted_output = run_output_payload(result)
    assert persisted_output["agent_tool_calls"][0]["tool_call_id"] == "agent-call-1"
    assert persisted_output["agent_tool_calls"][0]["target"]["profile_id"] == str(specialist_id)


def test_team_agent_tool_policy_is_frozen_and_hydrated_for_worker_request() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    specialist = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        instructions="Research the delegated input.",
        model="gpt-4.1",
        tool_policy={"allowed_tools": ["get_agent_inbox"]},
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Delegate specialist work.",
        model="gpt-4.1",
        tool_policy={"allowed_tools": ["get_agent_inbox"]},
    )
    session.add_all([manager, specialist])
    session.flush()
    manager.tool_policy = {
        "allowed_tools": ["get_agent_inbox"],
        "agent_tools": {
            "enabled": True,
            "allowed_agent_profile_ids": [str(specialist.id)],
            "max_depth": 1,
            "max_turns": 5,
            "max_targets": 1,
        },
    }
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Research Team",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=specialist.id,
            team_role="researcher",
            accepts_tasks=True,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Delegated research",
    )
    session.add(task)
    session.flush()
    settings = Settings(environment="test")
    builder = RunRequestBuilder(session, settings)
    snapshot = RunAuthorizationSnapshotService(
        session,
        builder,
    ).build_authorization_snapshot(task, None, manager)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=manager.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()

    request = builder.build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="agent-tool-build",
        ),
    )

    assert len(snapshot["agent_tools"]) == 1
    assert len(request.agent_tools) == 1
    agent_tool = request.agent_tools[0]
    assert agent_tool.target.ref.profile_id == specialist.id
    assert agent_tool.depth == 1
    assert agent_tool.max_depth == 1
    assert agent_tool.max_turns == 5
    assert agent_tool.context.allowed_tools == ("get_agent_inbox",)
    assert agent_tool.context.resource_grants == ()
    assert agent_tool.context.metadata["agent_tool"]["target_agent_profile_id"] == str(
        specialist.id
    )
    assert agent_tool.api_key == "sk-unit-test-provider"


def test_agent_tool_policy_rejects_target_outside_task_team() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Delegate.",
        model="gpt-4.1",
        tool_policy={"allowed_tools": []},
    )
    outsider = AgentProfile(
        workspace_id=workspace.id,
        name="Outsider",
        role="researcher",
        instructions="Do not expose.",
        model="gpt-4.1",
    )
    session.add_all([manager, outsider])
    session.flush()
    manager.tool_policy = {
        "allowed_tools": [],
        "agent_tools": {
            "enabled": True,
            "allowed_agent_profile_ids": [str(outsider.id)],
        },
    }
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Private Team",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Reject outsider",
    )
    session.add(task)
    session.flush()

    with pytest.raises(ValueError, match="task-accepting member"):
        RunAuthorizationSnapshotService(
            session,
            RunRequestBuilder(session, Settings(environment="test")),
        ).build_authorization_snapshot(task, None, manager)


def _tool(name: str) -> AgentRuntimeToolDefinition:
    return AgentRuntimeToolDefinition(
        name=name,
        source="product",
        description=f"Use {name}.",
        input_schema={"type": "object", "additionalProperties": True},
    )
