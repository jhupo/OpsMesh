"""Quota limit, usage and reservation HTTP contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NormalizedUsage(BaseModel):
    """Quantity-only operation usage; input tokens exclude the cached bucket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    tool_invocations: int = Field(default=0, ge=0)


class CapacityLimitUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit_value: int | None = Field(default=None, ge=0)


class CapacityKindView(BaseModel):
    kind: str
    limit_value: int | None
    reserved_value: int


class CapacitySnapshotView(BaseModel):
    user_id: str
    kinds: list[CapacityKindView]


class QuotaView(BaseModel):
    """One account's limits and current ledger position."""

    model_config = ConfigDict(extra="forbid")

    token_limit: int | None
    used_tokens: int
    reserved_tokens: int
    available_tokens: int | None
    storage_limit_bytes: int | None
    used_bytes: int
    reserved_bytes: int
    available_bytes: int | None


class QuotaLimitsRequest(BaseModel):
    """The administrator-writable allowances of one account."""

    model_config = ConfigDict(extra="forbid")

    token_limit: int | None = Field(default=None, ge=0)
    storage_limit_bytes: int | None = Field(default=None, ge=0)
