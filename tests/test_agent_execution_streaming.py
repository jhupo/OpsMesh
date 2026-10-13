import asyncio
from uuid import uuid4

import pytest
from agents import RunContextWrapper
from agents.items import ModelResponse
from agents.stream_events import RawResponsesStreamEvent
from agents.usage import Usage
from openai.types.responses import ResponseTextDeltaEvent

import opsmesh.agents.execution.providers.openai.streaming as openai_streaming
from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentRunRequest,
    ToolExecutionResult,
)
from opsmesh.agents.execution.errors import AgentExecutionCancelledError
from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor
from opsmesh.agents.profiles.models import AgentProfile


class TriggerCancellation:
    async def is_cancelled(self) -> bool:
        return False

    async def wait_cancelled(self) -> None:
        await asyncio.sleep(0)


class CancellableExecutor:
    def __init__(self) -> None:
        self.cancelled = False

    async def review_tool_call(self, **_: object) -> dict[str, object]:
        return {"decision": "allow", "risk_level": "low", "reasons": []}

    async def execute_tool(self, **_: object) -> ToolExecutionResult:
        return ToolExecutionResult(status="completed")

    async def cancel_active_tools(self, **_: object) -> None:
        self.cancelled = True


def test_openai_streaming_maps_ordered_events_lifecycle_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StreamResult:
        final_output = "done"
        context_wrapper = RunContextWrapper(
            context=None, usage=Usage(requests=1, input_tokens=7, output_tokens=3, total_tokens=10)
        )
        interruptions: list[object] = []
        new_items: list[object] = []
        last_agent = None

        def __init__(self, agent: object, hooks: object) -> None:
            self.agent = agent
            self.last_agent = agent
            self.hooks = hooks

        async def stream_events(self):
            await self.hooks.on_agent_start(None, self.agent)
            await self.hooks.on_llm_start(None, self.agent, None, [])
            yield RawResponsesStreamEvent(
                data=ResponseTextDeltaEvent(
                    type="response.output_text.delta",
                    delta="api_key=sk-stream-secret",
                    content_index=0,
                    output_index=0,
                    sequence_number=0,
                    item_id="offline",
                    logprobs=[],
                )
            )
            await self.hooks.on_llm_end(
                None,
                self.agent,
                ModelResponse(
                    output=[], usage=self.context_wrapper.usage, response_id="stream-response"
                ),
            )
            await self.hooks.on_agent_end(None, self.agent, "done")

        def cancel(self) -> None:
            raise AssertionError("completed stream must not be cancelled")

    def fake_run_streamed(agent: object, input_text: str, **kwargs: object) -> StreamResult:
        return StreamResult(agent, kwargs["hooks"])

    monkeypatch.setattr(openai_streaming.Runner, "run_streamed", fake_run_streamed)
    result = asyncio.run(OpenAIAgentExecutor().run(_openai_request(stream=True)))

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

    with pytest.raises(AgentExecutionCancelledError):
        asyncio.run(OpenAIAgentExecutor().run(request))

    assert sdk_cancelled.is_set()
    assert executor.cancelled is True


def _openai_request(**kwargs: object) -> AgentRunRequest:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="OpenAI",
        role="worker",
        instructions="Run.",
        model="gpt-4.1",
        model_settings={},
        id=uuid4(),
    )
    return AgentRunRequest(
        agent_profile=profile,
        input_text="Run.",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        api_key="sk-test",
        **kwargs,
        model_provider_credential_id=uuid4(),
    )
