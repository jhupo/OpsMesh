from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import replace

from backend.app.agent_runtime.cancellation import raise_if_cancelled
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
)
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver


class BaseSDKAgentRuntimeAdapter(ABC):
    """Template for product invariants shared by provider SDK adapters."""

    capabilities: AgentRuntimeCapabilities

    def _input_for_request(self, request: AgentRunRequest) -> str:
        if not request.continuations:
            return request.input_text
        continuation_lines = [
            "- "
            + json.dumps(
                {
                    "tool_name": continuation.tool_name,
                    "status": continuation.status,
                    "result": continuation.result
                    if continuation.status == "completed"
                    else continuation.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for continuation in request.continuations
        ]
        return (
            request.input_text
            + "\n\nCompleted runtime tool results:\n"
            + "\n".join(continuation_lines)
        )

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        self._validate_request(request)
        await raise_if_cancelled(request.cancellation)
        observer = AgentRuntimeExecutionObserver(request)
        observer.start()
        result = await self._run_once(request, observer)
        observer.finish(result)
        return replace(
            result,
            events=result.events + tuple(observer.events),
            stream_events=tuple(observer.stream_events),
            capabilities=self.capabilities,
        )

    @abstractmethod
    def _validate_request(self, request: AgentRunRequest) -> None: ...

    @abstractmethod
    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult: ...
