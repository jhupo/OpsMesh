from __future__ import annotations

from copy import deepcopy
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PolicyT = TypeVar("PolicyT", bound=BaseModel)


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
