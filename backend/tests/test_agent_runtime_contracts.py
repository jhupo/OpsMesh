from __future__ import annotations

import inspect
import json

import pytest

import backend.app.agent_runtime.contracts as runtime_contracts
from backend.app.agent_runtime.contracts import (
    AgentRuntimeAgentRef,
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeHandoff,
    AgentRuntimeSession,
    AgentRuntimeStreamEventKind,
    AgentRuntimeStructuredOutput,
)
from backend.app.agent_runtime.openai_results import OpenAIAgentsResultMapper


def test_runtime_contracts_do_not_import_vendor_result_or_state_types() -> None:
    source = inspect.getsource(runtime_contracts)

    assert "from agents" not in source
    assert "import agents" not in source


def test_runtime_capabilities_require_explicit_support() -> None:
    capabilities = AgentRuntimeCapabilities(
        provider="openai-compatible",
        adapter="openai_agents",
        supported=frozenset(
            {AgentRuntimeCapability.SESSIONS, AgentRuntimeCapability.RESUMABLE_STATE}
        ),
    )

    assert capabilities.supports(AgentRuntimeCapability.SESSIONS)
    assert capabilities.supports("resumable_state")
    assert not capabilities.supports(AgentRuntimeCapability.STREAMING)
    with pytest.raises(ValueError, match="streaming"):
        capabilities.require(AgentRuntimeCapability.STREAMING)


def test_handoff_and_session_contracts_are_vendor_neutral() -> None:
    class Session:
        session_id = "session-1"

        async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
            return []

        async def add_items(self, items: list[dict[str, object]]) -> None:
            return None

        async def pop_item(self) -> dict[str, object] | None:
            return None

        async def clear_session(self) -> None:
            return None

    handoff = AgentRuntimeHandoff(
        target=AgentRuntimeAgentRef(name="reviewer", role="quality"),
        reason="review the generated artifact",
        input_filter=("workspace_id", "run_id"),
    )

    assert isinstance(Session(), AgentRuntimeSession)
    assert handoff.target.name == "reviewer"
    assert handoff.input_filter == ("workspace_id", "run_id")


def test_openai_result_mapper_preserves_structured_output_and_redacts_stream_events() -> None:
    class Result:
        final_output = {"decision": "approved", "token": "api_key=sk-secret"}
        stream_events = [
            type(
                "Delta",
                (),
                {
                    "type": AgentRuntimeStreamEventKind.TEXT_DELTA.value,
                    "delta": "api_key=sk-secret",
                },
            )(),
            type("Completed", (), {"type": "run.completed"})(),
        ]

    mapper = OpenAIAgentsResultMapper()
    final_output, structured = mapper.final_output(Result())
    stream_events = mapper.stream_events(Result())

    assert json.loads(final_output) == {
        "decision": "approved",
        "token": "api_key=sk-secret",
    }
    assert isinstance(structured, AgentRuntimeStructuredOutput)
    assert structured.value == {
        "decision": "approved",
        "token": "api_key=sk-secret",
    }
    assert stream_events[0].delta == "[redacted]"
    assert stream_events[1].is_terminal is True
