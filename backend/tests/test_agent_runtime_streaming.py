import asyncio
from uuid import uuid4

import pytest
from claude_agent_sdk import ResultMessage, StreamEvent
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import sessionmaker

import backend.app.agent_runtime.openai_streaming as openai_streaming
from backend.app.agent_runtime.adapters.claude_agent import ClaudeAgentSDKRunner
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
from backend.app.agent_runtime.errors import AgentRuntimeCancelledError
from backend.app.agent_runtime.adapters.openai_agents import OpenAIAgentsRunner
from backend.app.agents.models import AgentProfile
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.run_cancellation import DatabaseRunCancellation
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workspaces.models import Workspace


class TriggerCancellation:
    async def is_cancelled(self) -> bool:
        return False

    async def wait_cancelled(self) -> None:
        await asyncio.sleep(0)


class CancellableExecutor:
    def __init__(self) -> None:
        self.cancelled = False

    def review_tool_call(self, **_: object) -> dict[str, object]:
        return {"decision": "allow", "risk_level": "low", "reasons": []}

    async def execute_tool(self, **_: object) -> AgentRuntimeToolResult:
        return AgentRuntimeToolResult(status="completed")

    async def cancel_active_tools(self, **_: object) -> None:
        self.cancelled = True


class FakeClaudeClient:
    def __init__(self, options: object, messages: list[object]) -> None:
        self.options = options
        self.messages = messages
        self.prompt = ""
        self.interrupted = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.prompt = prompt

    async def receive_response(self):
        for message in self.messages:
            yield message

    async def interrupt(self) -> None:
        self.interrupted.set()


def test_openai_streaming_maps_ordered_events_lifecycle_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StreamResult:
        final_output = "done"
        usage = {"requests": 1, "input_tokens": 7, "output_tokens": 3}
        interruptions: list[object] = []
        new_items: list[object] = []
        last_agent = None

        def __init__(self, agent: object, hooks: object) -> None:
            self.agent = agent
            self.hooks = hooks

        async def stream_events(self):
            await self.hooks.on_agent_start(None, self.agent)
            await self.hooks.on_llm_start(None, self.agent, None, [])
            yield type(
                "RawEvent",
                (),
                {
                    "type": "raw_response_event",
                    "data": type(
                        "TextDelta",
                        (),
                        {
                            "type": "response.output_text.delta",
                            "delta": "api_key=sk-stream-secret",
                        },
                    )(),
                },
            )()
            await self.hooks.on_llm_end(None, self.agent, object())
            await self.hooks.on_agent_end(None, self.agent, "done")

        def cancel(self) -> None:
            raise AssertionError("completed stream must not be cancelled")

    def fake_run_streamed(agent: object, input_text: str, **kwargs: object) -> StreamResult:
        return StreamResult(agent, kwargs["hooks"])

    monkeypatch.setattr(openai_streaming.Runner, "run_streamed", fake_run_streamed)
    result = asyncio.run(OpenAIAgentsRunner().run(_openai_request(stream=True)))

    assert [event.sequence for event in result.stream_events] == [1, 2, 3]
    assert [event.event_type for event in result.stream_events] == [
        "run.started",
        "output.text.delta",
        "run.completed",
    ]
    assert result.stream_events[1].delta == "[redacted]"
    assert result.stream_events[-1].is_terminal is True
    lifecycle_types = [
        event.event_type for event in result.events if event.event_type.startswith("agent.")
    ]
    assert lifecycle_types == [
        "agent.started",
        "agent.llm.started",
        "agent.llm.completed",
        "agent.completed",
    ]
    assert result.usage is not None
    assert result.usage.total_tokens == 10


def test_openai_cancellation_stops_sdk_run_and_active_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = CancellableExecutor()
    sdk_cancelled = asyncio.Event()

    class StreamResult:
        async def stream_events(self):
            await sdk_cancelled.wait()
            if False:
                yield None

        def cancel(self) -> None:
            sdk_cancelled.set()

    monkeypatch.setattr(
        openai_streaming.Runner,
        "run_streamed",
        lambda *args, **kwargs: StreamResult(),
    )
    request = _openai_request(
        stream=True,
        cancellation=TriggerCancellation(),
        tool_executor=executor,
    )

    with pytest.raises(AgentRuntimeCancelledError):
        asyncio.run(OpenAIAgentsRunner().run(request))

    assert sdk_cancelled.is_set()
    assert executor.cancelled is True


def test_claude_streaming_maps_ordered_events_and_usage() -> None:
    result_message = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
        result="done",
        usage={"input_tokens": 5, "output_tokens": 2},
        total_cost_usd=0.002,
    )
    client: FakeClaudeClient | None = None

    def factory(options: object) -> FakeClaudeClient:
        nonlocal client
        client = FakeClaudeClient(
            options,
            [
                StreamEvent(
                    uuid="event-1",
                    session_id=result_message.session_id,
                    event={"delta": {"text": "hello"}},
                ),
                result_message,
            ],
        )
        return client

    result = asyncio.run(
        ClaudeAgentSDKRunner(client_factory=factory).run(_claude_request(stream=True))
    )

    assert client is not None and client.prompt == "Run."
    assert client.options.include_partial_messages is True
    assert [event.sequence for event in result.stream_events] == [1, 2, 3]
    assert [event.event_type for event in result.stream_events] == [
        "run.started",
        "output.text.delta",
        "run.completed",
    ]
    assert result.usage is not None
    assert result.usage.total_tokens == 7
    assert result.usage.total_cost_usd == 0.002


def test_claude_cancellation_interrupts_client_and_active_tools() -> None:
    executor = CancellableExecutor()
    created: list[FakeClaudeClient] = []

    class WaitingClient(FakeClaudeClient):
        async def receive_response(self):
            await self.interrupted.wait()
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=1,
                session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
                terminal_reason="aborted_streaming",
            )

    def factory(options: object) -> WaitingClient:
        client = WaitingClient(options, [])
        created.append(client)
        return client

    request = _claude_request(
        stream=True,
        cancellation=TriggerCancellation(),
        tool_executor=executor,
    )
    with pytest.raises(AgentRuntimeCancelledError):
        asyncio.run(ClaudeAgentSDKRunner(client_factory=factory).run(request))

    assert len(created) == 1
    assert created[0].interrupted.is_set()
    assert executor.cancelled is True


def test_database_run_cancellation_observes_external_commit() -> None:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        owner = User(email="cancel@example.com", display_name="Owner")
        workspace = Workspace(owner=owner, name="Cancel", slug="cancel", settings={})
        session.add_all([owner, workspace])
        session.flush()
        run = AgentRun(
            workspace_id=workspace.id,
            status=RunStatus.RUNNING.value,
            input={},
        )
        session.add(run)
        session.commit()
        cancellation = DatabaseRunCancellation.for_session(
            session,
            workspace_id=workspace.id,
            run_id=run.id,
        )

        assert asyncio.run(cancellation.is_cancelled()) is False
        with sessions() as control_session:
            controlled_run = control_session.get(AgentRun, run.id)
            assert controlled_run is not None
            controlled_run.status = RunStatus.CANCELLED.value
            control_session.commit()
        assert asyncio.run(cancellation.is_cancelled()) is True


def _openai_request(**kwargs: object) -> AgentRunRequest:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="OpenAI",
        role="worker",
        instructions="Run.",
        model="gpt-4.1",
        model_settings={},
    )
    return AgentRunRequest(
        agent_profile=profile,
        input_text="Run.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        api_key="sk-test",
        **kwargs,
    )


def _claude_request(**kwargs: object) -> AgentRunRequest:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="worker",
        instructions="Run.",
        model="claude-sonnet-4-5",
    )
    return AgentRunRequest(
        agent_profile=profile,
        input_text="Run.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="anthropic",
        api_key="anthropic-test",
        **kwargs,
    )


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
