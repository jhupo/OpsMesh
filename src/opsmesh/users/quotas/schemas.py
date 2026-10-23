"""Quota limit, usage and reservation HTTP contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class QuotaLimitsRequest(BaseModel):
    """The administrator-writable allowances of one account."""

    model_config = ConfigDict(extra="forbid")

    token_limit: int | None = Field(default=None, ge=0)
