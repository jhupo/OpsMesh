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


class WorkingMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    max_entries: int = Field(default=64, ge=1, le=500)
    max_entry_tokens: int = Field(default=2_048, ge=128, le=16_384)


class EpisodicMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_enabled: bool = True
    retrieval_enabled: bool = True
    retention_days: int = Field(default=180, ge=1, le=3_650)
    max_results: int = Field(default=8, ge=1, le=50)
    default_importance: int = Field(default=30, ge=0, le=100)


class AgentMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_budget: ContextBudgetPolicy = Field(default_factory=ContextBudgetPolicy)
    working_memory: WorkingMemoryPolicy = Field(default_factory=WorkingMemoryPolicy)
    episodic_memory: EpisodicMemoryPolicy = Field(default_factory=EpisodicMemoryPolicy)


def context_budget_policy(memory_policy: object) -> ContextBudgetPolicy:
    return agent_memory_policy(memory_policy).context_budget


def default_context_budget_policy() -> dict[str, object]:
    return ContextBudgetPolicy().model_dump(mode="json")


def normalized_memory_policy(value: object) -> dict[str, object]:
    return agent_memory_policy(value).model_dump(mode="json")


def working_memory_policy(memory_policy: object) -> WorkingMemoryPolicy:
    return agent_memory_policy(memory_policy).working_memory


def episodic_memory_policy(memory_policy: object) -> EpisodicMemoryPolicy:
    return agent_memory_policy(memory_policy).episodic_memory


def agent_memory_policy(value: object) -> AgentMemoryPolicy:
    raw_policy = deepcopy(value) if isinstance(value, dict) else {}
    try:
        return AgentMemoryPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise ValueError(f"Invalid agent memory policy: {exc}") from exc
