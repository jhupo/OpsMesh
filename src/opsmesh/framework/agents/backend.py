"""Public SDK-neutral agent runtime port and capabilities."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from opsmesh.framework.typing import OpsMeshContract

from .context import AgentExecutionContext
from .event import AgentExecutionEvent
from .specification import AgentSpecification
from .state import BackendOutcome

EventSink = Callable[[AgentExecutionEvent], Awaitable[None]]


class AgentBackendCapabilities(OpsMeshContract):
    streaming: bool = False
    approvals: bool = False
    resume: bool = False
    structured_output: bool = False
    handoffs: bool = False
    agent_tools: bool = False
    sandbox: bool = False
    hosted_tools: tuple[str, ...] = ()
    native_tools: tuple[str, ...] = ()
    model_transports: tuple[str, ...] = ("http",)
    session_compaction: bool = False
    custom_tool_completion: bool = False
    structured_agent_input: bool = False
    pre_approval_guardrails: bool = False
    function_tool_concurrency: bool = False
    guardrail_stages: tuple[str, ...] = ()


class OpsMeshAgentBackend(Protocol):
    id: str
    version: str
    features: AgentBackendCapabilities

    async def run(
        self,
        spec: AgentSpecification,
        input: str | list[dict[str, Any]],
        context: AgentExecutionContext,
        *,
        checkpoint: dict[str, Any] | None = None,
        decisions: dict[str, bool] | None = None,
        emit: EventSink | None = None,
    ) -> BackendOutcome: ...
