from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_OUTPUT_RESERVE_TOKENS = 4_096
DEFAULT_SAFETY_MARGIN_TOKENS = 1_024


class ContextBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_input_tokens: int | None = Field(default=None, ge=1_024, le=1_000_000)
    context_window_tokens: int | None = Field(default=None, ge=4_096, le=2_000_000)
    output_reserve_tokens: int = Field(
        default=DEFAULT_OUTPUT_RESERVE_TOKENS,
        ge=256,
        le=262_144,
    )
    safety_margin_tokens: int = Field(
        default=DEFAULT_SAFETY_MARGIN_TOKENS,
        ge=128,
        le=65_536,
    )


def context_budget_policy(memory_policy: object) -> ContextBudgetPolicy:
    raw_policy = memory_policy if isinstance(memory_policy, dict) else {}
    raw_context = raw_policy.get("context_budget")
    try:
        return ContextBudgetPolicy.model_validate(
            raw_context if isinstance(raw_context, dict) else {}
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid agent context budget policy: {exc}") from exc


def default_context_budget_policy() -> dict[str, object]:
    return ContextBudgetPolicy().model_dump(mode="json")


def normalized_memory_policy(value: object) -> dict[str, object]:
    policy = deepcopy(value) if isinstance(value, dict) else {}
    policy["context_budget"] = context_budget_policy(policy).model_dump(mode="json")
    return policy
