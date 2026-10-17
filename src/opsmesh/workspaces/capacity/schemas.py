"""Workspace capacity HTTP contracts."""

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
    workspace_id: str
    kinds: list[CapacityKindView]
