"""Bounded declarative policy rules, without executable expressions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["allow", "require_approval", "deny"]


class RiskRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["tool.invoke", "runtime.command"]
    name: str | None = Field(default=None, min_length=1, max_length=200)
    decision: Decision


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    default: Decision = "allow"
    rules: list[RiskRule] = Field(default_factory=list, max_length=100)


class PolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    enabled: bool = True
    policy: RiskPolicy


class PolicyView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    revision: int
    enabled: bool
    policy: RiskPolicy
    updated_at: datetime


class PolicyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["tool.invoke", "runtime.command"]
    name: str = Field(min_length=1, max_length=200)


class PolicyDecision(BaseModel):
    decision: Decision
