from __future__ import annotations

from dataclasses import dataclass

from backend.app.agent_runtime.contracts import AgentRunResult


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
    usage = result.usage
    if usage is None:
        return NormalizedModelUsage(
            request_count=1,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
            raw_usage={},
            available=False,
        )
    return NormalizedModelUsage(
        request_count=max(1, usage.request_count),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=usage.total_tokens,
        raw_usage=usage.raw_usage,
        available=True,
    )
