"""Authorize every SDK callback against the detached, frozen run declaration."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeAgentTool,
    AgentRuntimeContext,
    AgentSessionBinding,
)
from backend.app.agents.execution.errors import AgentRuntimeCancelledError
from backend.app.runtime.agent_host.wire import CONTEXT, TOOL_RESULT
from backend.app.shared.utils import payload_hash


def allowed_contexts(request: AgentRunRequest) -> dict[str, AgentRuntimeContext]:
    contexts = [request.context]

    def collect(tools: Iterable[AgentRuntimeAgentTool]) -> None:
        for tool in tools:
            contexts.append(tool.context)
            collect(tool.nested_tools)

    collect(request.agent_tools)
    return {
        payload_hash(CONTEXT.dump_python(context, mode="json")): context for context in contexts
    }


@dataclass(frozen=True)
class RuntimeCallbackBroker:
    request: AgentRunRequest

    async def dispatch(self, method: str, payload: object) -> object:
        if not isinstance(payload, dict):
            raise ValueError("SDK callback requires an object payload")
        if method == "control.cancelled":
            return bool(
                self.request.cancellation and await self.request.cancellation.is_cancelled()
            )
        if method.startswith("session."):
            storage = self.request.session
            if storage is None or isinstance(storage, AgentSessionBinding):
                raise ValueError("SDK callback has no authorized Session")
            if method == "session.get":
                limit = payload.get("limit")
                if limit is not None and (
                    isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
                ):
                    raise ValueError("Session limit must be an integer")
                return await storage.get_items(limit)
            if method == "session.add":
                items = payload.get("items")
                if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                    raise ValueError("Session items must be objects")
                await storage.add_items(cast(list[dict[str, object]], items))
                return None
            if method == "session.pop":
                return await storage.pop_item()
            if method == "session.clear":
                await storage.clear_session()
                return None
            raise ValueError("Unsupported Session callback")
        executor = self.request.tool_executor
        if executor is None:
            raise ValueError("SDK callback has no authorized tool executor")
        encoded_context = payload.get("context")
        if not isinstance(encoded_context, dict):
            raise ValueError("SDK callback has no frozen tool context")
        context = allowed_contexts(self.request).get(payload_hash(encoded_context))
        if context is None:
            raise ValueError("SDK callback changed its authorized tool context")
        if method == "tool.cancel":
            await executor.cancel_active_tools(context=context)
            return None
        if self.request.cancellation and await self.request.cancellation.is_cancelled():
            raise AgentRuntimeCancelledError
        name, arguments = payload.get("tool_name"), payload.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError("Invalid SDK tool invocation")
        if method == "tool.review":
            return await executor.review_tool_call(
                context=context, tool_name=name, arguments=arguments
            )
        if method == "tool.execute":
            call_id, approved = payload.get("tool_call_id"), payload.get("approval_granted")
            if call_id is not None and not isinstance(call_id, str):
                raise ValueError("Invalid SDK call identity")
            if not isinstance(approved, bool):
                raise ValueError("Invalid SDK approval decision")
            result = await executor.execute_tool(
                context=context,
                tool_name=name,
                arguments=arguments,
                tool_call_id=call_id,
                approval_granted=approved,
            )
            return TOOL_RESULT.dump_python(result, mode="json")
        raise ValueError("Unsupported SDK callback")
