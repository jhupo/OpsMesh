"""Usage observation preserves cumulative SDK counters; pricing is a separate module."""

import inspect
from time import perf_counter
from typing import Any
from uuid import uuid4

from opsmesh.framework.agents import ModelUsageObservation
from opsmesh.framework.errors import OpsMeshError

from .serialization import serialize_sdk_value


def begin_request(context: Any, agent: Any, *, request_scope: Any = None) -> None:
    context.services.setdefault("pending_model_requests", []).append(
        {
            "call_id": str(uuid4()),
            # SDK start/end hooks run in different Tasks. The RunContextWrapper is
            # stable across those hooks and distinct for concurrent nested runners.
            "request_scope": agent if request_scope is None else request_scope,
            "started_monotonic": perf_counter(),
            "run_id": context.run_id,
            "agent_name": agent.name,
            "model": agent.model
            if isinstance(agent.model, str)
            else getattr(agent.model, "model", None),
            "model_profile_ref": context.services.get("agent_model_profiles", {}).get(agent.name),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "usage_available": False,
        }
    )


async def publish_unknown_usage(context: Any) -> None:
    pending = context.services.pop("pending_model_requests", [])
    if not pending:
        return
    for request in pending:
        request.pop("request_scope", None)
        started = request.pop("started_monotonic", None)
        if started is not None and (metrics := context.services.get("observe_metric")):
            metrics("model_duration_seconds", perf_counter() - started)
    prior = context.services.get("usage_snapshot", {})
    context.services["usage_snapshot"] = ModelUsageObservation.model_validate(
        dict(
            prior,
            requests=prior.get("requests", 0) + len(pending),
            usage_available=False,
            entries=[*prior.get("entries", ()), *pending],
        )
    ).model_dump(mode="json")
    observer = context.services.get("observe_usage")
    if observer is not None:
        for observation in pending:
            result = observer(observation)
            if inspect.isawaitable(result):
                await result


async def publish_request_usage(
    context: Any, agent: Any, response: Any, *, request_scope: Any = None
) -> None:
    observer = context.services.get("observe_usage")
    usage = response.usage
    pending = context.services.get("pending_model_requests", [])
    started: dict[str, Any] | None = None
    scope = agent if request_scope is None else request_scope
    for index, request in enumerate(pending):
        if request["agent_name"] == agent.name and request.get("request_scope") is scope:
            started = pending.pop(index)
            break
    if started is not None and (metrics := context.services.get("observe_metric")):
        metrics("model_duration_seconds", perf_counter() - started["started_monotonic"])
    model = agent.model if isinstance(agent.model, str) else getattr(agent.model, "model", None)
    details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    observation = {
        "call_id": response.response_id or (started or {}).get("call_id") or str(uuid4()),
        "response_id": response.response_id,
        "run_id": context.run_id,
        "model": model,
        "model_profile_ref": context.services.get("agent_model_profiles", {}).get(agent.name),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": getattr(details, "cached_tokens", 0),
        "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0),
        "provider_usage": _numeric_usage(usage),
        "total_tokens": usage.total_tokens,
        "usage_available": usage.requests > 0,
    }
    if not _record_completed_usage(context, observation, usage.requests):
        return
    if observer is not None:
        result = observer(observation)
        if inspect.isawaitable(result):
            await result
    await publish_hosted_usage(context, response, observation["call_id"])


def _record_completed_usage(context: Any, observation: dict[str, Any], requests: int) -> bool:
    prior = context.services.get("usage_snapshot", {})
    for entry in prior.get("entries", ()):
        if entry.get("call_id") == observation["call_id"]:
            if entry != observation:
                raise OpsMeshError("CONFLICT", "Provider response identity has different usage")
            return False
    context.services["usage_snapshot"] = ModelUsageObservation(
        requests=prior.get("requests", 0) + requests,
        input_tokens=prior.get("input_tokens", 0) + observation["input_tokens"],
        output_tokens=prior.get("output_tokens", 0) + observation["output_tokens"],
        total_tokens=prior.get("total_tokens", 0) + observation["total_tokens"],
        entries=(*prior.get("entries", ()), observation),
        usage_available=prior.get("usage_available", True) and requests > 0,
    ).model_dump(mode="json")
    return True


def _numeric_usage(usage: Any) -> dict[str, int | float]:
    """Retain numeric provider counters only; never persist response content here."""
    raw = serialize_sdk_value(usage)
    result: dict[str, int | float] = {}

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[prefix] = value

    visit(raw)
    return result


async def publish_hosted_usage(context: Any, response: Any, call_id: str) -> None:
    observer = context.services.get("observe_resource_usage")
    if observer is None:
        return
    kinds = {
        "web_search_call": "web_search",
        "file_search_call": "file_search",
        "code_interpreter_call": "code_interpreter",
        "image_generation_call": "image_generation",
        "mcp_call": "mcp",
        "tool_search_call": "tool_search",
    }
    for index, item in enumerate(getattr(response, "output", ())):
        resource = kinds.get(getattr(item, "type", ""))
        if resource is None:
            continue
        identity = getattr(item, "id", None) or f"{call_id}:{index}"
        result = observer(
            {
                "call_id": f"hosted:{identity}",
                "resource_key": f"hosted:{resource}",
                "unit": "call",
                "quantity": "1",
                "usage_available": True,
            }
        )
        if inspect.isawaitable(result):
            await result


def observe_usage(result: Any) -> ModelUsageObservation:
    usage = result.context_wrapper.usage
    return ModelUsageObservation(
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        entries=tuple(serialize_sdk_value(item) for item in usage.request_usage_entries),
    )
