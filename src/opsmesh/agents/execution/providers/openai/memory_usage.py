"""Account for model calls made by the SDK's memory generation lifecycle."""

from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

from agents.items import ModelResponse, TResponseStreamEvent
from agents.models.interface import Model
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from agents.usage import Usage

memory_usage: ContextVar[Usage | None] = ContextVar("opsmesh_sdk_memory_usage", default=None)


class MeteredMemoryModel(Model):
    def __init__(self, model: Model, usage: Usage) -> None:
        self.model = model
        self.usage = usage

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        response = await self.model.get_response(*args, **kwargs)
        self.usage.add(response.usage)
        return response

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        async for event in self.model.stream_response(*args, **kwargs):
            if event.type == "response.completed":
                response_usage = event.response.usage
                if response_usage is not None:
                    self.usage.add(
                        Usage(
                            requests=1,
                            input_tokens=response_usage.input_tokens,
                            output_tokens=response_usage.output_tokens,
                            total_tokens=response_usage.total_tokens,
                            input_tokens_details=response_usage.input_tokens_details,
                            output_tokens_details=response_usage.output_tokens_details,
                        )
                    )
            yield event

    async def close(self) -> None:
        await self.model.close()

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        return self.model.get_retry_advice(request)
