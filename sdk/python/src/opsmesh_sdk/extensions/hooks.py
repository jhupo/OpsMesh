"""Hook chain contracts: fixed stages, decisions and failure policy.

The chain runs before A -> before B -> revalidation -> action -> after B ->
after A. Decisions are Continue, Patch (stage-whitelisted fields) or Block.
There is no implicit Wait: long work becomes an explicit operation or job.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from opsmesh_sdk.manifest import HookFailurePolicy, HookStage

MAX_PATCH_FIELDS: Final = 32


class Continue(BaseModel):
    """Proceed without changing the input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["continue"] = "continue"
    observation: str | None = Field(default=None, max_length=2000)


class Patch(BaseModel):
    """Apply a stage-whitelisted patch; the chain revalidates before acting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["patch"] = "patch"
    fields: dict[str, JsonValue] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def enforce_field_bound(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > MAX_PATCH_FIELDS:
            raise ValueError(f"a hook patches at most {MAX_PATCH_FIELDS} fields")
        return value


class Block(BaseModel):
    """Terminate the current operation with a typed reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["block"] = "block"
    reason: str = Field(min_length=1, max_length=2000)
    code: str = Field(default="hook_blocked", min_length=1, max_length=100)


HookDecision = Continue | Patch | Block


class HookInput(BaseModel):
    """The typed input a hook receives for one stage occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: HookStage
    binding_id: str = Field(min_length=1, max_length=128)
    invocation_id: str | None = Field(default=None, max_length=128)
    occurrence: int = Field(ge=0)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class HookBinding:
    """A frozen binding: export, order, stages and failure policy."""

    def __init__(
        self,
        binding_id: str,
        export_id: str,
        stages: tuple[HookStage, ...],
        order: int,
        failure_policy: HookFailurePolicy = HookFailurePolicy.BLOCK,
    ) -> None:
        if order < 0:
            raise ValueError("hook order must be non-negative")
        self.binding_id = binding_id
        self.export_id = export_id
        self.stages = stages
        self.order = order
        self.failure_policy = failure_policy

    def sort_key(self) -> tuple[int, str]:
        return (self.order, self.export_id)


def validate_decision(decision: HookDecision, *, patch_fields: tuple[str, ...]) -> HookDecision:
    """Reject patches that touch fields outside the declared whitelist."""
    if isinstance(decision, Patch):
        outside = sorted(set(decision.fields) - set(patch_fields))
        if outside:
            raise ValueError(f"hook patches undeclared fields: {', '.join(outside)}")
    return decision


__all__ = [
    "Block",
    "Continue",
    "HookBinding",
    "HookDecision",
    "HookInput",
    "Patch",
    "validate_decision",
]
