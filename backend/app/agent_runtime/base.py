from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

from backend.app.agent_runtime.cancellation import raise_if_cancelled
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver
from backend.app.core.resilience import CircuitBreakerConfig, async_retry_with_circuit


class BaseSDKAgentRuntimeAdapter(ABC):
    """Template for product invariants shared by provider SDK adapters."""

    capabilities: AgentRuntimeCapabilities

    def __init__(
        self,
        *,
        max_attempts: int,
        circuit_config: CircuitBreakerConfig,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._circuit_config = circuit_config

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        self._validate_request(request)
        await raise_if_cancelled(request.cancellation)

        async def invoke() -> AgentRunResult:
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

        return await async_retry_with_circuit(
            key=self._provider_circuit_key(request),
            func=invoke,
            max_attempts=self._max_attempts,
            circuit_config=self._circuit_config,
            should_retry=lambda exc: normalize_agent_error(exc).retryable,
        )

    @abstractmethod
    def _validate_request(self, request: AgentRunRequest) -> None: ...

    @abstractmethod
    def _provider_circuit_key(self, request: AgentRunRequest) -> str: ...

    @abstractmethod
    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult: ...
