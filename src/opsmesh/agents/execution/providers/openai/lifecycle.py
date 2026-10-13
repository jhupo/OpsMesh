from __future__ import annotations

from typing import Any

from agents.lifecycle import RunHooksBase

from opsmesh.agents.execution.cancellation import raise_if_cancelled
from opsmesh.agents.execution.contracts import AgentCancellation, AgentRunRequest
from opsmesh.agents.execution.errors import AgentExecutionPolicyError
from opsmesh.agents.execution.observer import AgentExecutionObserver
from opsmesh.agents.execution.providers.openai.model_errors import active_model_request
from opsmesh.agents.execution.providers.openai.model_usage import record_model_usage
from opsmesh.agents.execution.providers.openai.sandbox import ProjectFilesCapability


class AgentExecutionHooks(RunHooksBase[Any, Any]):
    def __init__(
        self,
        observer: AgentExecutionObserver,
        cancellation: AgentCancellation | None,
        *,
        collect_project_outputs: bool = False,
    ) -> None:
        self._observer = observer
        self._cancellation = cancellation
        self._collect_project_outputs = collect_project_outputs

    @classmethod
    def for_request(cls, request: AgentRunRequest) -> AgentExecutionHooks:
        return cls(AgentExecutionObserver(request), request.cancellation)

    async def on_llm_start(
        self,
        context: Any,
        agent: Any,
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        await self._checkpoint()
        request = getattr(agent, "runtime_request", None)
        active = active_model_request.get()
        if request is not None and active is not None:
            active[:] = [request]
        if request is not None and request.tool_executor is not None:
            await request.tool_executor.authorize_agent(context=request.context)
        await self._record("agent.llm.started", agent)

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        await self._checkpoint()
        request = getattr(agent, "runtime_request", None)
        if request is not None:
            record_model_usage(request, response.usage, response.response_id)
        await self._record("agent.llm.completed", agent)

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        await self._checkpoint()
        await self._record("agent.started", agent)

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        await self._checkpoint()
        request = getattr(agent, "runtime_request", None)
        if request is not None and request.require_handoff:
            raise AgentExecutionPolicyError(
                code="router_handoff_required",
                message="Routing-only agent must hand off the response",
                event_type="runtime.authorization_blocked",
                metadata={"agent_profile_id": str(request.agent_profile.id)},
            )
        if self._collect_project_outputs:
            for capability in getattr(agent, "capabilities", ()):
                if isinstance(capability, ProjectFilesCapability):
                    await capability.harvest()
        await self._record("agent.completed", agent)

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        await self._checkpoint()
        await self._observer.lifecycle(
            "agent.handoff.started",
            "Agent handoff started.",
            {
                "source_agent": _agent_name(from_agent),
                "target_agent": _agent_name(to_agent),
            },
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        await self._checkpoint()
        await self._record(
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
        result: object,
    ) -> None:
        await self._checkpoint()
        await self._record(
            "agent.tool.completed",
            agent,
            tool_name=str(getattr(tool, "name", "")),
            tool_call_id=_tool_call_id(context),
        )

    async def _checkpoint(self) -> None:
        await raise_if_cancelled(self._cancellation)

    async def _record(self, event_type: str, agent: Any, **payload: object) -> None:
        await self._observer.lifecycle(
            event_type,
            event_type.replace(".", " ").capitalize() + ".",
            {
                "agent": _agent_name(agent),
                "agent_profile_id": str(agent.runtime_ref.profile_id)
                if getattr(agent, "runtime_ref", None)
                else None,
                **payload,
            },
        )


def _agent_name(agent: object) -> str:
    ref = getattr(agent, "runtime_ref", None)
    return str(ref.name if ref else getattr(agent, "name", agent))


def _tool_call_id(context: object) -> str | None:
    value = getattr(context, "tool_call_id", None)
    return value if isinstance(value, str) and value else None
