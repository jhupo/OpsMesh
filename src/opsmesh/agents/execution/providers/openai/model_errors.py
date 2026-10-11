"""Preserve SDK retries while attributing failures to the executing graph model."""

from collections.abc import AsyncIterator
from typing import Any

from agents.items import ModelResponse, TResponseStreamEvent
from agents.models.interface import Model
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from openai import OpenAIError

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.errors import AgentRuntimePolicyError


class GraphModel(Model):
    def __init__(self, model: Model, request: AgentRunRequest) -> None:
        self.delegate = model
        self.model = request.model or request.agent_profile.model
        self.request = request

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        try:
            return await self.delegate.get_response(*args, **kwargs)
        except OpenAIError as exc:
            self._attribute(exc)
            raise

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        try:
            async for event in self.delegate.stream_response(*args, **kwargs):
                yield event
        except OpenAIError as exc:
            self._attribute(exc)
            raise

    def _attribute(self, exc: OpenAIError) -> None:
        # Keep the vendor exception intact so SDK retry advice still sees its type.
        exc._opsmesh_graph_request = self.request  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self.delegate.close()

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        return self.delegate.get_retry_advice(request)


def graph_model_error(exc: Exception, request: AgentRunRequest) -> AgentRuntimePolicyError:
    failed = getattr(exc, "_opsmesh_graph_request", request)
    if not isinstance(failed, AgentRunRequest):
        raise TypeError("Graph model failure attribution is invalid")
    profile = failed.agent_profile
    return AgentRuntimePolicyError(
        code="agent_graph_model_failed",
        message="An agent model request failed; the graph was stopped without replay",
        event_type="agent.model.failed",
        metadata={
            "agent_profile_id": str(profile.id) if profile.id else None,
            "model": failed.model or profile.model,
            "provider": failed.provider,
            "model_provider_credential_id": (
                str(failed.model_provider_credential_id)
                if failed.model_provider_credential_id
                else None
            ),
            "status_code": getattr(exc, "status_code", None),
            "replay_allowed": False,
        },
    )
