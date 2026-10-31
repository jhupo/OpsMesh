"""Translate neutral visibility, history filters and nested-output callbacks."""

import inspect
from typing import Any

from opsmesh.framework.agents import AgentRunCompleted, get_current_execution_context
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import HandoffHistory, RuntimeExtensions

from .serialization import serialize_sdk_value
from .usage import observe_usage


def visibility_callback(
    ref: str | None, target: dict[str, Any], extensions: RuntimeExtensions
) -> Any:
    if ref is None:
        return True
    callback = extensions.visibility(ref)

    async def enabled(wrapper: Any, agent: Any) -> bool:
        value = callback(wrapper.context, target)
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, bool):
            raise OpsMeshError("CONFIG_INVALID", "Visibility callbacks must return a boolean")
        return value

    return enabled


def history_filter_callback(ref: str, extensions: RuntimeExtensions) -> Any:
    callback = extensions.handoff_filter(ref)

    def select(items: tuple[Any, ...], indices: tuple[int, ...] | None) -> tuple[Any, ...]:
        if indices is None:
            return items
        if any(index < 0 or index >= len(items) for index in indices) or len(set(indices)) != len(
            indices
        ):
            raise OpsMeshError("CONFIG_INVALID", "Handoff history selection is out of range")
        return tuple(items[index] for index in indices)

    async def filter_input(data: Any) -> Any:
        context = data.run_context.context if data.run_context else get_current_execution_context()
        history = HandoffHistory(
            input_history=serialize_sdk_value(data.input_history),
            prior_items=tuple(
                serialize_sdk_value(item.to_input_item()) for item in data.pre_handoff_items
            ),
            new_items=tuple(serialize_sdk_value(item.to_input_item()) for item in data.new_items),
        )
        result = callback(context, history)
        selection = await result if inspect.isawaitable(result) else result
        selected = select(data.new_items, selection.new_indices)
        return data.clone(
            input_history=selection.input_history
            if selection.input_history is not None
            else data.input_history,
            pre_handoff_items=select(data.pre_handoff_items, selection.prior_indices),
            new_items=data.new_items if selection.preserve_history else selected,
            input_items=selected,
        )

    return filter_input


def output_extractor_callback(ref: str, extensions: RuntimeExtensions) -> Any:
    callback = extensions.output_extractor(ref)

    async def extract(result: Any) -> str:
        outcome = AgentRunCompleted(
            output=serialize_sdk_value(result.final_output),
            history=tuple(serialize_sdk_value(item) for item in result.to_input_list()),
            usage=observe_usage(result),
        )
        value = callback(result.context_wrapper.context, outcome)
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, str):
            raise OpsMeshError("CONFIG_INVALID", "Agent tool output extractors must return text")
        return value

    return extract
