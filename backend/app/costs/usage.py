from __future__ import annotations

from dataclasses import dataclass

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.security.redaction import redact_sensitive_payload


@dataclass(frozen=True)
class NormalizedModelUsage:
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    raw_usage: dict[str, object]
    available: bool


def normalize_model_usage(result: AgentRunResult) -> NormalizedModelUsage:
    payloads = _usage_payloads(result)
    input_tokens = sum(_first_int(raw, "input_tokens", "prompt_tokens") for raw in payloads)
    output_tokens = sum(
        _first_int(raw, "output_tokens", "completion_tokens") for raw in payloads
    )
    cached_input_tokens = sum(
        max(
            _first_int(raw, "cache_read_input_tokens", "cached_input_tokens"),
            _nested_int(raw, "input_tokens_details", "cached_tokens"),
        )
        for raw in payloads
    )
    reasoning_tokens = sum(
        max(
            _first_int(raw, "reasoning_tokens"),
            _nested_int(raw, "output_tokens_details", "reasoning_tokens"),
        )
        for raw in payloads
    )
    total_tokens = sum(
        _first_int(raw, "total_tokens")
        or _first_int(raw, "input_tokens", "prompt_tokens")
        + _first_int(raw, "output_tokens", "completion_tokens")
        for raw in payloads
    )
    request_count = sum(_first_int(raw, "requests", "request_count") or 1 for raw in payloads)
    raw_usage: dict[str, object]
    if len(payloads) == 1:
        raw_usage = redact_sensitive_payload(payloads[0])
    elif payloads:
        raw_usage = redact_sensitive_payload({"calls": payloads})
    else:
        raw_usage = {}
    return NormalizedModelUsage(
        request_count=max(1, request_count),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=min(cached_input_tokens, input_tokens),
        reasoning_tokens=min(reasoning_tokens, output_tokens),
        total_tokens=max(total_tokens, input_tokens + output_tokens),
        raw_usage=raw_usage,
        available=bool(payloads),
    )


def _usage_payloads(result: AgentRunResult) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for event in result.events:
        if event.event_type != "model.usage":
            continue
        usage = event.payload.get("usage")
        if isinstance(usage, dict) and usage:
            payloads.append({str(key): value for key, value in usage.items()})
    if payloads:
        return payloads
    if isinstance(result.raw_output, dict):
        usage = result.raw_output.get("usage")
        if isinstance(usage, dict) and usage:
            return [{str(key): value for key, value in usage.items()}]
    return []


def _first_int(value: dict[str, object], *keys: str) -> int:
    for key in keys:
        parsed = _positive_int(value.get(key))
        if parsed is not None:
            return parsed
    return 0


def _nested_int(value: dict[str, object], outer_key: str, inner_key: str) -> int:
    nested = value.get(outer_key)
    if not isinstance(nested, dict):
        return 0
    return _positive_int(nested.get(inner_key)) or 0


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None
