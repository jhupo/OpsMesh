"""Mutable OpenAI-only state; platform services never inspect these fields."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from opsmesh.framework.agents import AgentExecutionContext, AgentExecutionEvent
from opsmesh.framework.errors import OpsMeshError


@dataclass
class OpenAIExecutionState:
    sandbox_client: Any = None
    sandbox_options: Any = None
    compaction_client: Any = None
    backend_event_sink: Callable[[AgentExecutionEvent], Awaitable[None]] | None = None
    model_profile: dict[str, Any] | None = None
    model_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_model_profiles: dict[str, str | None] = field(default_factory=dict)
    pending_model_requests: list[dict[str, Any]] = field(default_factory=list)


def execution_state(context: AgentExecutionContext) -> OpenAIExecutionState:
    if context.backend_state is None:
        context.backend_state = OpenAIExecutionState()
    if not isinstance(context.backend_state, OpenAIExecutionState):
        raise OpsMeshError("CONTEXT_INVALID", "Execution state belongs to a different backend")
    return context.backend_state
