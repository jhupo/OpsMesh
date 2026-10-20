"""Official Model and ModelProvider adapters over explicit Activity-local operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol
from uuid import UUID

import httpx2
from agents import Model, ModelProvider, ModelResponse, OpenAIProvider
from agents.items import TResponseStreamEvent
from agents.usage import Usage
from openai import AsyncOpenAI
from pydantic import TypeAdapter
from temporalio import activity

from opsmesh.orchestration.execution.contracts import ModelConnection, UncertainOperationError
from opsmesh.shared.errors import ConflictError

RESPONSE = TypeAdapter(ModelResponse)
STREAM = TypeAdapter(list[TResponseStreamEvent])


def model_usage(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens - usage.input_tokens_details.cached_tokens,
        "output_tokens": usage.output_tokens,
        "cached_input_tokens": usage.input_tokens_details.cached_tokens,
        "tool_invocations": 0,
    }


class ModelIO(Protocol):
    def has_hooks(self) -> bool: ...
    async def before_hooks(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], operation_id: str
    ) -> tuple[tuple[Any, ...], dict[str, Any], list[str]]: ...
    async def after_hooks(
        self, result: ModelResponse, operation_id: str, entered: list[str]
    ) -> ModelResponse: ...
    def claim(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], operation_id: str, streaming: bool
    ) -> tuple[bool, Any, ModelConnection | None, str]: ...
    def complete(self, operation_id: str, result: Any, usage: dict[str, int] | None) -> None: ...
    async def finish_hooks(self, operation_id: str, entered: list[str], failed: bool) -> None: ...


class NativeModelProvider(ModelProvider):
    def __init__(self, operations: Callable[[UUID, str], ModelIO]) -> None:
        self.operations = operations

    def get_model(self, model_name: str | None) -> Model:
        if model_name is None:
            raise ValueError("A frozen execution model reference is required")
        run_id, agent_key = model_name.split(":", 1)
        return NativeModel(self.operations(UUID(run_id), agent_key))


class NativeModel(Model):
    def __init__(self, operations: ModelIO) -> None:
        self.operations = operations

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        info = activity.info()
        operation_id = f"{info.workflow_id}:{info.activity_id}"
        entered: list[str] = []
        claimed = False
        failed = False
        try:
            args, kwargs, entered = await self.operations.before_hooks(args, kwargs, operation_id)
            claimed, cached, connection, model_name = await asyncio.to_thread(
                self.operations.claim, args, kwargs, operation_id, False
            )
            if not claimed:
                return await self.operations.after_hooks(
                    RESPONSE.validate_python(cached), operation_id, entered
                )
            assert connection is not None
            async with AsyncOpenAI(
                api_key=connection.api_key,
                base_url=connection.base_url,
                max_retries=0,
                timeout=120,
                http_client=httpx2.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                ),
            ) as client:
                native = OpenAIProvider(openai_client=client).get_model(model_name)
                result = await native.get_response(*args, **kwargs)
            await asyncio.to_thread(
                self.operations.complete,
                operation_id,
                RESPONSE.dump_python(result, mode="json"),
                model_usage(result.usage),
            )
            return await self.operations.after_hooks(result, operation_id, entered)
        except BaseException:
            failed = True
            if claimed:
                await asyncio.shield(
                    asyncio.to_thread(self.operations.complete, operation_id, None, None)
                )
            raise
        finally:
            await self.operations.finish_hooks(operation_id, entered, failed)

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        if await asyncio.to_thread(self.operations.has_hooks):
            raise ConflictError(
                "Installed output checks require the non-streaming native model boundary"
            )
        info = activity.info()
        operation_id = f"{info.workflow_id}:{info.activity_id}"
        claimed, cached, connection, model_name = await asyncio.to_thread(
            self.operations.claim, args, kwargs, operation_id, True
        )
        if not claimed:
            for event in STREAM.validate_python(cached):
                yield event
            return
        assert connection is not None
        events: list[TResponseStreamEvent] = []
        usage: dict[str, int] | None = None
        try:
            async with AsyncOpenAI(
                api_key=connection.api_key,
                base_url=connection.base_url,
                max_retries=0,
                timeout=120,
                http_client=httpx2.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                ),
            ) as client:
                native = OpenAIProvider(openai_client=client).get_model(model_name)
                async for event in native.stream_response(*args, **kwargs):
                    events.append(event)
                    if event.type == "response.completed" and event.response.usage is not None:
                        actual = event.response.usage
                        usage = {
                            "input_tokens": actual.input_tokens
                            - actual.input_tokens_details.cached_tokens,
                            "output_tokens": actual.output_tokens,
                            "cached_input_tokens": actual.input_tokens_details.cached_tokens,
                            "tool_invocations": 0,
                        }
                    yield event
            await asyncio.to_thread(
                self.operations.complete,
                operation_id,
                STREAM.dump_python(events, mode="json"),
                usage,
            )
            if usage is None:
                raise UncertainOperationError("The provider did not confirm actual usage")
        except BaseException:
            await asyncio.shield(
                asyncio.to_thread(self.operations.complete, operation_id, None, None)
            )
            raise
