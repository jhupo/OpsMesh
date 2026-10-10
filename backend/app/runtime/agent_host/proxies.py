"""Transport-only implementations of SDK public callbacks, with no history or agent loop."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from agents.memory.session_settings import SessionSettings

from backend.app.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.runtime.agent_host.wire import CONTEXT, TOOL_RESULT

RpcCall = Callable[[str, dict[str, object]], Awaitable[object]]


@dataclass(frozen=True)
class RpcSession:
    session_id: str
    call: RpcCall
    session_settings: SessionSettings | None = None

    async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], await self.call("session.get", {"limit": limit}))

    async def add_items(self, items: list[dict[str, object]]) -> None:
        await self.call("session.add", {"items": items})

    async def pop_item(self) -> dict[str, object] | None:
        return cast(dict[str, object] | None, await self.call("session.pop", {}))

    async def clear_session(self) -> None:
        await self.call("session.clear", {})


@dataclass(frozen=True)
class RpcTools:
    call: RpcCall

    async def review_tool_call(
        self, *, context: AgentRuntimeContext, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        return cast(
            dict[str, object],
            await self.call(
                "tool.review",
                {
                    "context": CONTEXT.dump_python(context, mode="json"),
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
            ),
        )

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> AgentRuntimeToolResult:
        return TOOL_RESULT.validate_python(
            await self.call(
                "tool.execute",
                {
                    "context": CONTEXT.dump_python(context, mode="json"),
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "tool_call_id": tool_call_id,
                    "approval_granted": approval_granted,
                },
            )
        )

    async def cancel_active_tools(self, *, context: AgentRuntimeContext) -> None:
        await self.call("tool.cancel", {"context": CONTEXT.dump_python(context, mode="json")})


@dataclass(frozen=True)
class RpcCancellation:
    call: RpcCall

    async def is_cancelled(self) -> bool:
        return await self.call("control.cancelled", {}) is True

    async def wait_cancelled(self) -> None:
        while not await self.is_cancelled():
            await asyncio.sleep(0.25)
