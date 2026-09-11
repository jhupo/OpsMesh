from __future__ import annotations

from backend.app.agent_runtime.core.contracts import AgentRuntimeUsage
from backend.app.security.redaction import redact_sensitive_payload


def runtime_usage(
    usage: object,
    *,
    model_usage: object = None,
    total_cost_usd: object = None,
    request_count: int | None = None,
) -> AgentRuntimeUsage | None:
    payloads = _payloads(usage)
    if not payloads:
        payloads = _model_payloads(model_usage)
    if not payloads and total_cost_usd is None:
        return None
    input_tokens = sum(
        _first_int(item, "input_tokens", "inputTokens", "prompt_tokens")
        for item in payloads
    )
    output_tokens = sum(
        _first_int(item, "output_tokens", "outputTokens", "completion_tokens")
        for item in payloads
    )
    cached_tokens = sum(
        max(
            _first_int(
                item,
                "cache_read_input_tokens",
                "cacheReadInputTokens",
                "cached_input_tokens",
            ),
            _nested_int(item, "input_tokens_details", "cached_tokens"),
        )
        for item in payloads
    )
    reasoning_tokens = sum(
        max(
            _first_int(item, "reasoning_tokens"),
            _nested_int(item, "output_tokens_details", "reasoning_tokens"),
        )
        for item in payloads
    )
    total_tokens = sum(
        _first_int(item, "total_tokens")
        or _first_int(item, "input_tokens", "inputTokens", "prompt_tokens")
        + _first_int(item, "output_tokens", "outputTokens", "completion_tokens")
        for item in payloads
    )
    requests = request_count
    if requests is None:
        requests = sum(
            _first_int(item, "requests", "request_count") or 1 for item in payloads
        )
    cost = _nonnegative_float(total_cost_usd)
    raw: dict[str, object] = {}
    if len(payloads) == 1:
        raw = payloads[0]
    elif payloads:
        raw = {"calls": payloads}
    if cost is not None:
        raw = dict(raw)
        raw["total_cost_usd"] = cost
    return AgentRuntimeUsage(
        request_count=max(1, requests or 0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=min(cached_tokens, input_tokens),
        reasoning_tokens=min(reasoning_tokens, output_tokens),
        total_tokens=max(total_tokens, input_tokens + output_tokens),
        total_cost_usd=cost,
        raw_usage=redact_sensitive_payload(raw),
    )


def _payloads(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict) and value:
        return [{str(key): item for key, item in value.items()}]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict) and dumped:
            return [{str(key): item for key, item in dumped.items()}]
    return []


def _model_payloads(value: object) -> list[dict[str, object]]:
    if not isinstance(value, dict):
        return []
    return [
        {str(key): item for key, item in payload.items()}
        for payload in value.values()
        if isinstance(payload, dict) and payload
    ]


def _first_int(value: dict[str, object], *keys: str) -> int:
    for key in keys:
        parsed = _nonnegative_int(value.get(key))
        if parsed is not None:
            return parsed
    return 0


def _nested_int(value: dict[str, object], outer_key: str, inner_key: str) -> int:
    nested = value.get(outer_key)
    return _first_int(nested, inner_key) if isinstance(nested, dict) else 0


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None


def _nonnegative_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0.0, float(value))
