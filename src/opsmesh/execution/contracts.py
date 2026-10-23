"""Detached values shared by execution drivers, without an orchestration engine."""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from agents import RunState
from opsmesh_sdk.identity import InvocationSource
from pydantic import BaseModel, ConfigDict, Field

from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.agents.tools import ToolInfo
from opsmesh.execution.preparation import AgentSnapshot


class ExecutionValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionIdentity(ExecutionValue):
    run_id: UUID
    user_id: UUID


class SessionReference(ExecutionValue):
    session_id: UUID
    generation: int = Field(default=1, ge=1)


class PreparedAgent(ExecutionValue):
    identity: ExecutionIdentity
    instance_id: UUID
    snapshot: AgentSnapshot
    session: SessionReference | None = None
    environment_id: UUID | None = None
    max_turns: int = Field(default=32, ge=1, le=256)
    source: InvocationSource = Field(default_factory=InvocationSource)
    tool_descriptions: dict[str, ToolInfo] | None = None
    provider_configs: dict[str, ProviderConfig] | None = None


class HistoryCall(ExecutionValue):
    identity: ExecutionIdentity
    reference: SessionReference
    method: Literal["get_items", "add_items", "pop_item", "clear_session"]
    items: list[dict[str, object]] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class AgentOutcome:
    status: Literal["completed", "interrupted"]
    output: object = None
    state: RunState[Any] | None = None
    pending: list[dict[str, object]] = field(default_factory=list)
