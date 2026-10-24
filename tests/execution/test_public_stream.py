"""Only public handoff paths may publish intermediate model text."""

from opsmesh.execution.preparation import AgentSnapshot
from opsmesh.execution.runs.streaming import public_agent_ids


def test_tool_agent_and_its_handoffs_are_excluded_from_public_stream() -> None:
    snapshot = AgentSnapshot.model_validate(
        {
            "root_id": "main",
            "definitions": {
                "main": {
                    "type": "agent",
                    "name": "Main",
                    "handoffs": [{"agent_id": "answer"}],
                    "tools": [{"type": "agent", "agent_id": "research"}],
                },
                "answer": {"type": "agent", "name": "Answer"},
                "research": {
                    "type": "agent",
                    "name": "Research",
                    "handoffs": [{"agent_id": "private"}],
                },
                "private": {"type": "agent", "name": "Private"},
            },
        }
    )
    assert public_agent_ids(snapshot) == {"main", "answer"}


def test_an_agent_used_as_both_tool_and_handoff_is_not_public() -> None:
    snapshot = AgentSnapshot.model_validate(
        {
            "root_id": "main",
            "definitions": {
                "main": {
                    "type": "agent",
                    "name": "Main",
                    "handoffs": [{"agent_id": "shared"}],
                    "tools": [{"type": "agent", "agent_id": "shared"}],
                },
                "shared": {"type": "agent", "name": "Shared"},
            },
        }
    )
    assert public_agent_ids(snapshot) == {"main"}
