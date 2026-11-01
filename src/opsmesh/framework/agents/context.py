"""Public process-local execution context; never serialize live services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from opsmesh.framework.errors import OpsMeshError

from .resources import AgentExecutionResources, AgentExecutionServices, AgentExecutionState
from .specification import AgentSpecification

ToolCall = Callable[[str, dict[str, Any], str], Awaitable[Any]]
AgentResolver = Callable[[str], Awaitable[AgentSpecification]]
_execution_context: ContextVar[AgentExecutionContext | None] = ContextVar(
    "opsmesh_execution_context", default=None
)


def get_current_execution_context() -> AgentExecutionContext:
    context = _execution_context.get()
    if context is None:
        raise OpsMeshError("CONTEXT_UNAVAILABLE", "No agent execution is active in this task")
    return context


@contextmanager
def bind_execution_context(context: AgentExecutionContext) -> Iterator[None]:
    token = _execution_context.set(context)
    try:
        yield
    finally:
        _execution_context.reset(token)


@dataclass
class AgentExecutionContext:
    run_id: str
    session_id: str
    principal_id: str
    invoke_tool: ToolCall
    resolve_agent: AgentResolver | None = None
    services: AgentExecutionServices = field(default_factory=AgentExecutionServices)
    resources: AgentExecutionResources = field(default_factory=AgentExecutionResources)
    state: AgentExecutionState = field(default_factory=AgentExecutionState)
    backend_state: object | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
        }
