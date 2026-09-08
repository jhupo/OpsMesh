import asyncio
import json
from dataclasses import replace
from uuid import uuid4

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from backend.app.agent_runtime.claude_agent import (
    ClaudeAgentSDKRunner,
    ClaudeAgentSessionStore,
    _session_id,
)
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeApprovalDecision,
    AgentRuntimeContext,
    AgentRuntimeOutputSchema,
    AgentRuntimeResumeState,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
)
from backend.app.agents.models import AgentProfile


def _request(**kwargs: object) -> AgentRunRequest:
    workspace_id = uuid4()
    profile = AgentProfile(
        workspace_id=workspace_id,
        name="Claude",
        role="researcher",
        instructions="Use the authorized runtime tools.",
        model="claude-sonnet-4-5",
    )
    context = AgentRuntimeContext(
        workspace_id=workspace_id,
        task_id=None,
        run_id=uuid4(),
        allowed_tools=("search_docs",),
        tool_definitions=(
            AgentRuntimeToolDefinition(
                name="search_docs",
                source="mcp",
                description="Search the workspace documentation.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ),
    )
    executor = kwargs.pop("tool_executor", RecordingExecutor())
    return AgentRunRequest(
        agent_profile=profile,
        input_text="Search the docs.",
        context=context,
        provider="anthropic",
        api_key="anthropic-test-key",
        tool_executor=executor,
        **kwargs,
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        self.calls.append((tool_name, arguments))
        return AgentRuntimeToolResult(status="completed", output={"answer": "found"})


class FakeClaudeClient:
    def __init__(self, options: object, query_fn: object) -> None:
        self.options = options
        self.query_fn = query_fn
        self.prompt = ""
        self.interrupted = False

    async def __aenter__(self) -> "FakeClaudeClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.prompt = prompt

    async def receive_response(self):
        async for message in self.query_fn(prompt=self.prompt, options=self.options):
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True


def _client_factory(query_fn: object):
    return lambda options: FakeClaudeClient(options, query_fn)


def test_claude_agent_sdk_runner_maps_result_usage_and_structured_output() -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object):
        captured["prompt"] = prompt
        captured["options"] = options
        yield AssistantMessage(
            content=[TextBlock(text="done")],
            model="claude-sonnet-4-5",
            session_id="not-used",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
            result='{"answer":"done"}',
            structured_output={"answer": "done"},
            usage={"input_tokens": 4, "output_tokens": 2},
            total_cost_usd=0.001,
        )

    request = _request(
        output_schema=AgentRuntimeOutputSchema(
            name="answer",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )
    )
    result = asyncio.run(
        ClaudeAgentSDKRunner(client_factory=_client_factory(fake_query)).run(request)
    )

    assert result.final_output == '{"answer": "done"}'
    assert result.structured_output is not None
    assert result.structured_output.validated is True
    assert result.events[0].event_type == "model.request"
    assert any(event.event_type == "model.usage" for event in result.events)
    assert captured["prompt"] == "Search the docs."
    assert captured["options"].tools == []
    assert captured["options"].strict_mcp_config is True


def test_claude_agent_sdk_runner_uses_mcp_tool_bridge_and_policy_review() -> None:
    captured: dict[str, object] = {}

    class Executor(RecordingExecutor):
        def review_tool_call(self, **_: object) -> dict[str, object]:
            return {"decision": "allow", "risk_level": "low"}

    async def fake_query(*, prompt: str, options: object):
        captured["options"] = options
        yield AssistantMessage(
            content=[
                ToolUseBlock(
                    id="call-1",
                    name="mcp__opsmesh__search_docs",
                    input={"query": "sdk"},
                )
            ],
            model="claude-sonnet-4-5",
            session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
            result="found",
        )

    request = _request(tool_executor=Executor())
    result = asyncio.run(
        ClaudeAgentSDKRunner(client_factory=_client_factory(fake_query)).run(request)
    )

    assert result.final_output == "found"
    assert result.capabilities is not None
    assert result.capabilities.adapter == "claude_agent_sdk"
    assert any(event.event_type == "tool.call" for event in result.events)


def test_claude_agent_sdk_runner_maps_deferred_tool_to_resume_state() -> None:
    async def fake_query(*, prompt: str, options: object):
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="9f7b7b61-8915-4d07-b4f9-e2cb0d5b53f0",
            result="approval required",
            deferred_tool_use=type(
                "Deferred",
                (),
                {
                    "id": "call-approval",
                    "name": "mcp__opsmesh__search_docs",
                    "input": {"query": "sensitive"},
                },
            )(),
        )

    request = _request()
    result = asyncio.run(
        ClaudeAgentSDKRunner(client_factory=_client_factory(fake_query)).run(request)
    )

    assert len(result.interruptions) == 1
    interruption = result.interruptions[0]
    assert interruption.tool_call_id == "call-approval"
    assert interruption.tool_name == "search_docs"
    assert result.resume_state is not None
    assert json.loads(result.resume_state.serialized_state)["tool_call_id"] == "call-approval"


def test_claude_agent_sdk_runner_rejects_approval_without_resuming_session() -> None:
    base_request = _request()
    state = AgentRuntimeResumeState(
        provider="claude_agent_sdk",
        serialized_state=json.dumps(
            {
                "session_id": _session_id(base_request),
                "tool_call_id": "call-approval",
                "tool_name": "search_docs",
            }
        ),
    )
    request = replace(
        base_request,
        resume_state=state,
        approval_decisions=(
            AgentRuntimeApprovalDecision(
                tool_call_id="call-approval",
                tool_name="search_docs",
                status="rejected",
                reason="operator denied",
            ),
        ),
    )
    result = asyncio.run(
        ClaudeAgentSDKRunner(client_factory=_client_factory(_unexpected_query)).run(request)
    )

    assert result.final_output == "operator denied"
    assert result.events[0].event_type == "tool.rejected"


async def _unexpected_query(**_: object):
    raise AssertionError("rejected approval must not invoke Claude")
    yield  # pragma: no cover


def test_claude_agent_session_store_mirrors_only_claude_entries() -> None:
    class Session:
        def __init__(self) -> None:
            self.items: list[dict[str, object]] = []

        async def add_items(self, items: list[dict[str, object]]) -> None:
            self.items.extend(items)

        async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
            return self.items

    session = Session()
    store = ClaudeAgentSessionStore(session, session_id="session-1")
    key = {"project_key": "project", "session_id": "session-1"}
    asyncio.run(store.append(key, [{"type": "user", "uuid": "entry-1"}]))

    loaded = asyncio.run(store.load(key))

    assert loaded == [{"type": "user", "uuid": "entry-1"}]
    assert session.items[0]["_opsmesh_runtime"] == "claude_agent_sdk"
