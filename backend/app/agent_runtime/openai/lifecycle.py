from __future__ import annotations

from typing import Any

from agents.lifecycle import RunHooksBase

from backend.app.agent_runtime.cancellation import raise_if_cancelled
from backend.app.agent_runtime.contracts import (
    AgentRuntimeCancellation,
)
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver


class OpenAIRuntimeHooks(RunHooksBase[Any, Any]):
    def __init__(
        self,
        observer: AgentRuntimeExecutionObserver,
        cancellation: AgentRuntimeCancellation | None,
    ) -> None:
        self._observer = observer
        self._cancellation = cancellation

    async def on_llm_start(
        self,
        context: Any,
        agent: Any,
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        await self._checkpoint()
        self._record("agent.llm.started", agent)

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        await self._checkpoint()
        self._record("agent.llm.completed", agent)

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        await self._checkpoint()
        self._record("agent.started", agent)

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        await self._checkpoint()
        self._record("agent.completed", agent)

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        await self._checkpoint()
        self._observer.lifecycle(
            "agent.handoff.started",
            "Agent handoff started.",
            {
                "source_agent": _agent_name(from_agent),
                "target_agent": _agent_name(to_agent),
            },
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        await self._checkpoint()
        self._record(
            "agent.tool.started",
            agent,
            tool_name=str(getattr(tool, "name", "")),
            tool_call_id=_tool_call_id(context),
        )

    async def on_tool_end(
        self,
        context: Any,
        agent: Any,
        tool: Any,
        result: str,
    ) -> None:
        await self._checkpoint()
        self._record(
            "agent.tool.completed",
            agent,
            tool_name=str(getattr(tool, "name", "")),
            tool_call_id=_tool_call_id(context),
        )

    async def _checkpoint(self) -> None:
        await raise_if_cancelled(self._cancellation)

    def _record(self, event_type: str, agent: Any, **payload: object) -> None:
        self._observer.lifecycle(
            event_type,
            event_type.replace(".", " ").capitalize() + ".",
            {"agent": _agent_name(agent), **payload},
        )


def _agent_name(agent: object) -> str:
    return str(getattr(agent, "name", agent))


def _tool_call_id(context: object) -> str | None:
    value = getattr(context, "tool_call_id", None)
    return value if isinstance(value, str) and value else None
