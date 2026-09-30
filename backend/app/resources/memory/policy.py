from __future__ import annotations

from copy import deepcopy
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PolicyT = TypeVar("PolicyT", bound=BaseModel)

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


class SemanticMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval_enabled: bool = True
    write_enabled: bool = True
    max_results: int = Field(default=8, ge=1, le=50)


class ContextMemoryRetrievalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_results: int = Field(default=12, ge=1, le=50)
    max_context_tokens: int = Field(default=4_096, ge=256, le=32_768)
    query_max_tokens: int = Field(default=1_024, ge=128, le=8_192)


class AgentMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_budget: ContextBudgetPolicy = Field(default_factory=ContextBudgetPolicy)
    working_memory: WorkingMemoryPolicy = Field(default_factory=WorkingMemoryPolicy)
    episodic_memory: EpisodicMemoryPolicy = Field(default_factory=EpisodicMemoryPolicy)
    semantic_memory: SemanticMemoryPolicy = Field(default_factory=SemanticMemoryPolicy)
    context_retrieval: ContextMemoryRetrievalPolicy = Field(
        default_factory=ContextMemoryRetrievalPolicy
    )


class HybridMemoryRetrievalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_text_weight: float = Field(default=1.0, gt=0, le=10)
    vector_weight: float = Field(default=1.0, gt=0, le=10)
    lexical_weight: float = Field(default=0.35, gt=0, le=10)
    reciprocal_rank_constant: int = Field(default=60, ge=1, le=1_000)
    candidate_multiplier: int = Field(default=4, ge=1, le=20)
    importance_weight: float = Field(default=0.15, ge=0, le=1)
    recency_weight: float = Field(default=0.1, ge=0, le=1)
    recency_half_life_days: int = Field(default=30, ge=1, le=3_650)


class MemoryLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episodic_decay_half_life_days: int = Field(default=90, ge=1, le=3_650)
    semantic_decay_half_life_days: int = Field(default=365, ge=1, le=7_300)
    archive_expired_episodes: bool = True
    semantic_archive_after_days: int | None = Field(default=None, ge=30, le=7_300)
    auto_promote_episodes: bool = False
    promotion_min_importance: int = Field(default=75, ge=0, le=100)
    promotion_min_access_count: int = Field(default=3, ge=1, le=10_000)


def hybrid_retrieval_policy(value: object) -> HybridMemoryRetrievalPolicy:
    return _validated_policy(HybridMemoryRetrievalPolicy, value, "retrieval")


def memory_lifecycle_policy(value: object) -> MemoryLifecyclePolicy:
    return _validated_policy(MemoryLifecyclePolicy, value, "lifecycle")


def default_retrieval_policy() -> dict[str, object]:
    return HybridMemoryRetrievalPolicy().model_dump(mode="json")


def default_lifecycle_policy() -> dict[str, object]:
    return MemoryLifecyclePolicy().model_dump(mode="json")


def _validated_policy(
    policy_type: type[PolicyT],
    value: object,
    label: str,
) -> PolicyT:
    raw = deepcopy(value) if isinstance(value, dict) else {}
    try:
        return policy_type.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid memory {label} policy: {exc}") from exc


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


def semantic_memory_policy(memory_policy: object) -> SemanticMemoryPolicy:
    return agent_memory_policy(memory_policy).semantic_memory


def context_memory_retrieval_policy(memory_policy: object) -> ContextMemoryRetrievalPolicy:
    return agent_memory_policy(memory_policy).context_retrieval


def agent_memory_policy(value: object) -> AgentMemoryPolicy:
    raw_policy = deepcopy(value) if isinstance(value, dict) else {}
    try:
        return AgentMemoryPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise ValueError(f"Invalid agent memory policy: {exc}") from exc
