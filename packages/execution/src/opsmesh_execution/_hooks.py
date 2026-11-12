"""Compose native SDK hooks with framework-managed plugin callbacks."""

from typing import Any

from agents import RunHooks

from .callbacks import ExecutionCallbacks


class ExecutionHooks(RunHooks[Any]):
    """Preserve native hooks and then invoke plugin callbacks."""

    def __init__(
        self,
        caller: RunHooks[Any] | None,
        *,
        callbacks: ExecutionCallbacks,
    ) -> None:
        """Keep caller hooks and the per-run callback scope."""
        self._caller = caller
        self._callbacks = callbacks

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        """Forward the native agent-start event."""
        if self._caller is not None:
            await self._caller.on_agent_start(context, agent)
        await self._callbacks.emit("on_agent_start", context, agent)

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        """Forward the agent-end event to the caller."""
        if self._caller is not None:
            await self._caller.on_agent_end(context, agent, output)
        await self._callbacks.emit("on_agent_end", context, agent, output)

    async def on_handoff(
        self, context: Any, from_agent: Any, to_agent: Any
    ) -> None:
        """Forward the handoff event to the caller."""
        if self._caller is not None:
            await self._caller.on_handoff(context, from_agent, to_agent)
        await self._callbacks.emit("on_handoff", context, from_agent, to_agent)

    async def on_llm_start(
        self, context: Any, agent: Any, system_prompt: Any, input_items: Any
    ) -> None:
        """Forward the model-start event to the caller."""
        if self._caller is not None:
            await self._caller.on_llm_start(
                context, agent, system_prompt, input_items
            )
        await self._callbacks.emit(
            "on_llm_start", context, agent, system_prompt, input_items
        )

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        """Forward the native response, including its original Usage."""
        if self._caller is not None:
            await self._caller.on_llm_end(context, agent, response)
        await self._callbacks.emit("on_llm_end", context, agent, response)

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        """Forward the tool-start event to the caller."""
        if self._caller is not None:
            await self._caller.on_tool_start(context, agent, tool)
        await self._callbacks.emit("on_tool_start", context, agent, tool)

    async def on_tool_end(
        self, context: Any, agent: Any, tool: Any, result: Any
    ) -> None:
        """Forward the tool-end event to the caller."""
        if self._caller is not None:
            await self._caller.on_tool_end(context, agent, tool, result)
        await self._callbacks.emit("on_tool_end", context, agent, tool, result)
