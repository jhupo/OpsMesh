"""Adapt plugin lifecycle notifications to the installed SDK's Agent hooks."""

import asyncio
from types import MappingProxyType
from typing import Any

from agents import Agent
from agents.items import ModelResponse, TResponseInputItem
from agents.lifecycle import AgentHooksBase
from agents.run_context import AgentHookContext, RunContextWrapper
from agents.tool import Tool
from opsmesh_sdk.hooks import AgentHookEvent, HookPoint, HookRegistration


class PluginAgentHooks(AgentHooksBase[Any, Agent[Any]]):
    def __init__(self, registrations: tuple[HookRegistration, ...]) -> None:
        self.registrations = tuple(sorted(registrations, key=lambda item: item.priority))

    async def _emit(self, point: HookPoint, context: Any, agent: Agent[Any], **data: Any) -> None:
        event = AgentHookEvent(point, context, agent, MappingProxyType(data))
        callbacks = self.registrations
        if point in {"agent_end", "tool_end", "llm_end"}:
            callbacks = tuple(reversed(callbacks))
        for item in callbacks:
            if item.point == point:
                async with asyncio.timeout(item.timeout_seconds):
                    result = await item.handler(event)
                if result is not None:
                    raise TypeError(
                        "Lifecycle hooks return None; use middleware for transformations"
                    )

    async def on_start(self, context: AgentHookContext[Any], agent: Agent[Any]) -> None:
        await self._emit("agent_start", context, agent)

    async def on_end(self, context: AgentHookContext[Any], agent: Agent[Any], output: Any) -> None:
        await self._emit("agent_end", context, agent, output=output)

    async def on_handoff(
        self, context: RunContextWrapper[Any], agent: Agent[Any], source: Agent[Any]
    ) -> None:
        await self._emit("handoff", context, agent, source=source)

    async def on_tool_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any], tool: Tool
    ) -> None:
        await self._emit("tool_start", context, agent, tool=tool)

    async def on_tool_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], tool: Tool, result: object
    ) -> None:
        await self._emit("tool_end", context, agent, tool=tool, result=result)

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        await self._emit(
            "llm_start", context, agent, system_prompt=system_prompt, input_items=input_items
        )

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse
    ) -> None:
        await self._emit("llm_end", context, agent, response=response)
