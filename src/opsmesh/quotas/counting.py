"""Preflight input accounting through the pinned OpenAI SDK's native request builder."""

from inspect import signature
from typing import Any

from agents import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI, Omit

COUNT_FIELDS = frozenset(
    {
        "conversation",
        "input",
        "instructions",
        "model",
        "parallel_tool_calls",
        "personality",
        "previous_response_id",
        "reasoning",
        "text",
        "tool_choice",
        "tools",
        "truncation",
    }
)


class InputTokenCounter:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def count(self, model: Model, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
        if not isinstance(model, (OpenAIResponsesModel, OpenAIChatCompletionsModel)):
            raise ValueError("Managed token accounting requires a native OpenAI SDK model")
        values = signature(model.get_response).bind(*args, **kwargs).arguments
        values.pop("tracing")
        # Reuse the locked SDK's tool, handoff, schema and multimodal conversion.
        # Chat mode counts its Responses representation through the same provider.
        native = (
            model
            if isinstance(model, OpenAIResponsesModel)
            else OpenAIResponsesModel(model=model.model, openai_client=self.client)
        )
        request = native._build_response_create_kwargs(**values)
        request.update(request.get("extra_body") or {})
        if not isinstance(request["prompt"], Omit):
            raise ValueError(
                "Managed input counting requires explicit input instead of a saved prompt"
            )
        result = await self.client.responses.input_tokens.count(
            **{key: value for key, value in request.items() if key in COUNT_FIELDS}
        )
        if type(result.input_tokens) is not int or result.input_tokens < 0:
            raise ValueError("Provider returned an invalid input token count")
        return result.input_tokens
