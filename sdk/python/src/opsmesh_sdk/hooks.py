"""Async lifecycle notifications; these callbacks do not make policy decisions."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

HookPoint = Literal[
    "agent_start", "agent_end", "handoff", "tool_start", "tool_end", "llm_start", "llm_end"
]
HOOK_POINTS: tuple[HookPoint, ...] = (
    "agent_start",
    "agent_end",
    "handoff",
    "tool_start",
    "tool_end",
    "llm_start",
    "llm_end",
)


@dataclass(frozen=True)
class AgentHookEvent:
    """Native SDK context and Agent are borrowed for this callback invocation only."""

    point: HookPoint
    context: Any = field(repr=False)
    agent: Any = field(repr=False)
    data: Mapping[str, Any] = field(default_factory=dict, repr=False)


HookHandler = Callable[[AgentHookEvent], Awaitable[object]]


@dataclass(frozen=True)
class HookRegistration:
    point: HookPoint
    handler: HookHandler
    priority: int = 100
    timeout_seconds: float = 30
