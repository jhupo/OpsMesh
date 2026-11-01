"""Public run settings and deterministic, non-mutating model setting composition."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract


class ModelRetryBackoffOverrides(OpsMeshContract):
    initial_delay: float | None = Field(default=None, ge=0, le=300, allow_inf_nan=False)
    max_delay: float | None = Field(default=None, ge=0, le=300, allow_inf_nan=False)
    multiplier: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    jitter: bool | None = Field(default=None, strict=True)


class ModelRetryOverrides(OpsMeshContract):
    max_retries: int | None = Field(default=None, ge=0, le=5, strict=True)
    backoff: ModelRetryBackoffOverrides | None = None


class ModelReasoningOverrides(OpsMeshContract):
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    summary: Literal["auto", "concise", "detailed"] | None = None


class ModelExtraArgumentsOverrides(OpsMeshContract):
    """Only provider options that cannot replace input, tools, credentials or transport."""

    service_tier: Literal["auto", "default", "flex", "priority"] | None = None
    prompt_cache_key: str | None = Field(default=None, min_length=1, max_length=64)


class ModelSettingsOverrides(OpsMeshContract):
    """A run can change these settings; omitted/None values inherit the prior layer."""

    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    top_p: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2, allow_inf_nan=False)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000, strict=True)
    parallel_tool_calls: bool | None = Field(default=None, strict=True)
    truncation: Literal["auto", "disabled"] | None = None
    verbosity: Literal["low", "medium", "high"] | None = None
    timeout: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)
    reasoning: ModelReasoningOverrides | None = None
    retry: ModelRetryOverrides | None = None
    extra_args: ModelExtraArgumentsOverrides | None = None

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.retry and self.retry.backoff:
            backoff = self.retry.backoff
            if (
                backoff.initial_delay is not None
                and backoff.max_delay is not None
                and backoff.initial_delay > backoff.max_delay
            ):
                raise ValueError("Retry initial_delay must not exceed max_delay")
        return self


_PROTECTED_REQUEST_ARGUMENTS = frozenset(
    {
        "model",
        "input",
        "messages",
        "instructions",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
        "text",
        "response_format",
        "max_output_tokens",
        "max_completion_tokens",
        "reasoning_effort",
        "conversation",
        "previous_response_id",
        "prompt",
        "include",
        "extra_body",
        "extra_headers",
        "extra_query",
        "extra_args",
        "api_key",
        "base_url",
        "authorization",
        "headers",
        "timeout",
        "metadata",
        "store",
        "prompt_cache_retention",
        "include_usage",
        "response_include",
        "top_logprobs",
        "context_management",
        "prompt_cache_options",
        "preserve_raw_usage",
    }
) | frozenset(ModelSettingsOverrides.model_fields)


def merge_model_settings(*layers: dict[str, Any]) -> dict[str, Any]:
    """Deep merge dictionaries, retain explicit scalar nulls, and reject ambiguous extras."""

    result: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = _merge_mapping(result[key], value)
            else:
                result[key] = deepcopy(value)
    extras = result.get("extra_args")
    if extras is not None:
        if not isinstance(extras, dict):
            raise OpsMeshError("CONFIG_INVALID", "extra_args must be an object")
        conflicts = set(extras) & (_PROTECTED_REQUEST_ARGUMENTS | set(result))
        if conflicts:
            raise OpsMeshError(
                "MODEL_SETTINGS_CONFLICT",
                "extra_args cannot replace mapped request fields",
                details={"fields": sorted(conflicts)},
            )
    retry = result.get("retry")
    if isinstance(retry, dict) and isinstance(retry.get("backoff"), dict):
        backoff = retry["backoff"]
        for name in ("initial_delay", "max_delay", "multiplier"):
            value = backoff.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise OpsMeshError("CONFIG_INVALID", "Retry delays must be numeric")
        if (
            backoff.get("initial_delay") is not None
            and backoff.get("max_delay") is not None
            and backoff["initial_delay"] > backoff["max_delay"]
        ):
            raise OpsMeshError("MODEL_SETTINGS_CONFLICT", "Retry initial_delay exceeds max_delay")
    return result


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = (
            _merge_mapping(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else deepcopy(value)
        )
    return result
