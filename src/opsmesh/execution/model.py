"""Native Model wrapper for authorization, bounded quota and effect receipts."""

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

from agents import FunctionTool, Model, ModelResponse, ModelSettings
from agents.items import TResponseStreamEvent
from agents.usage import Usage
from openai.types.responses import ResponseCompletedEvent
from pydantic import TypeAdapter

from opsmesh.agents.config.model import ModelRef
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.platform.billing.contracts import ModelCostRequest, ModelPricing
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.serialization import canonical_json

RESPONSE = TypeAdapter(ModelResponse)


def _contains_media(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("type") in {"input_image", "input_file", "input_audio"} or any(
            _contains_media(item) for item in value.values()
        )
    return isinstance(value, list) and any(_contains_media(item) for item in value)


class ExecutionModel(Model):
    def __init__(
        self,
        native: Model,
        ledger: OperationLedger,
        *,
        operation_id: Callable[[], str] | None = None,
        context_tokens: int | None = None,
        timeout_seconds: float = 300,
        reference: ModelRef | None = None,
        pricing: ModelPricing | None = None,
        max_retries: int = 0,
    ) -> None:
        self.native, self.ledger = native, ledger
        self.context_tokens = context_tokens
        self.timeout_seconds = timeout_seconds
        self.reference, self.pricing, self.max_retries = reference, pricing, max_retries
        self.operation_id = operation_id or (
            lambda: ledger.context.operation_id("model", uuid4().hex)
        )

    def _prepare(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[inspect.BoundArguments, str, dict[str, object] | None]:
        bound = inspect.signature(Model.get_response).bind(self, *args, **kwargs)
        values = bound.arguments
        values["input"] = TypeAdapter(Any).dump_python(values["input"], mode="json")
        settings = values["model_settings"]
        settings = settings.resolve(ModelSettings(preserve_raw_usage=True))
        values["model_settings"] = settings
        if settings.max_tokens is None or settings.max_tokens <= 0:
            raise InvalidArgumentError("Execution models require an explicit max_tokens bound")
        payload = {
            "instructions": values["system_instructions"],
            "input": values["input"],
            "settings": settings.to_json_dict(),
            "previous_response_id": values.get("previous_response_id"),
            "conversation_id": values.get("conversation_id"),
            "prompt": values.get("prompt"),
            "tools": [
                {"name": tool.name, "schema": getattr(tool, "params_json_schema", {})}
                for tool in values["tools"]
            ],
            "handoffs": [
                {"name": item.tool_name, "schema": item.input_json_schema}
                for item in values["handoffs"]
            ],
            "output_schema": values["output_schema"].json_schema()
            if values["output_schema"]
            else None,
        }
        operation_id = self.operation_id()
        estimate = len(canonical_json(payload).encode()) + 4096 + settings.max_tokens
        if self.context_tokens is not None and estimate > self.context_tokens:
            raise InvalidArgumentError("The model request exceeds its declared context limit")
        if (
            values.get("prompt") is not None
            or values.get("previous_response_id") is not None
            or values.get("conversation_id") is not None
            or any(not isinstance(tool, FunctionTool) for tool in values["tools"])
            or _contains_media(values["input"])
        ):
            if self.context_tokens is None:
                raise InvalidArgumentError(
                    "Media, remote history, prompts and hosted tools require model context_tokens"
                )
            estimate = self.context_tokens
        cached = self.ledger.claim(
            operation_id,
            "model",
            payload,
            tokens=estimate,
            cost=ModelCostRequest(
                provider_id=self.reference.provider_id if self.reference else None,
                model_id=self.reference.model_id if self.reference else None,
                pricing=self.pricing,
                input_bound=estimate,
                output_bound=settings.max_tokens,
                attempts=self.max_retries + 1,
            ),
        )
        return bound, operation_id, cached

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        bound, operation_id, cached = self._prepare(args, kwargs)
        if cached is not None:
            return RESPONSE.validate_python(cached)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.native.get_response(*bound.args[1:], **bound.kwargs)
            self._validate_usage(response)
            self.ledger.complete(
                operation_id,
                RESPONSE.dump_python(response, mode="json"),
                tokens=response.usage.input_tokens + response.usage.output_tokens,
            )
            return response
        except BaseException as error:
            # A failed response does not establish that the provider did not bill.
            self.ledger.uncertain(operation_id, error_code=type(error).__name__)
            raise

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        bound, operation_id, cached = self._prepare(args, kwargs)
        if cached is not None:
            raise InvalidArgumentError("A completed model stream cannot be replayed as live events")
        completed = False
        stream = self.native.stream_response(*bound.args[1:], **bound.kwargs)
        try:
            async for event in self._bounded_stream(stream):
                if isinstance(event, ResponseCompletedEvent):
                    usage = event.response.usage
                    if usage is None:
                        raise InvalidArgumentError(
                            "The model stream did not include confirmed usage"
                        )
                    if usage.input_tokens + usage.output_tokens <= 0:
                        raise InvalidArgumentError(
                            "The model stream did not confirm positive token usage"
                        )
                    response = ModelResponse(
                        output=event.response.output,
                        response_id=event.response.id,
                        usage=Usage(
                            requests=1,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            total_tokens=usage.total_tokens,
                            input_tokens_details=usage.input_tokens_details,
                            output_tokens_details=usage.output_tokens_details,
                        ),
                        raw_usage=usage.model_dump(mode="json"),
                    )
                    self.ledger.complete(
                        operation_id,
                        RESPONSE.dump_python(response, mode="json"),
                        tokens=usage.input_tokens + usage.output_tokens,
                    )
                    completed = True
                yield event
            if not completed:
                raise InvalidArgumentError("The model stream ended without a completion receipt")
        finally:
            if not completed:
                self.ledger.uncertain(operation_id)
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    @staticmethod
    def _validate_usage(response: ModelResponse) -> None:
        raw = response.raw_usage
        if raw is None:
            raise InvalidArgumentError("The model did not preserve confirmed provider usage")
        input_tokens = raw.get("input_tokens", raw.get("prompt_tokens"))
        output_tokens = raw.get("output_tokens", raw.get("completion_tokens"))
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or input_tokens < 0
            or output_tokens < 0
            or input_tokens != response.usage.input_tokens
            or output_tokens != response.usage.output_tokens
        ):
            raise InvalidArgumentError("The model usage is incomplete or inconsistent")

    async def _bounded_stream(
        self, stream: AsyncIterator[TResponseStreamEvent]
    ) -> AsyncIterator[TResponseStreamEvent]:
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    event = await anext(stream)
            except StopAsyncIteration:
                return
            yield event
