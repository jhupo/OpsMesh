"""Public neutral execution outcomes and usage observations."""

from typing import Any, Literal

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ModelUsageObservation(OpsMeshContract):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    entries: tuple[dict[str, Any], ...] = ()
    cumulative: bool = True
    usage_available: bool = True


class PendingToolApproval(OpsMeshContract):
    server_label: str | None = None
    id: str
    tool_name: str
    agent_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool | None = None


class AgentRunCompleted(OpsMeshContract):
    kind: Literal["completed"] = "completed"
    output: Any = None
    usage: ModelUsageObservation = Field(default_factory=ModelUsageObservation)
    history: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunInterrupted(OpsMeshContract):
    kind: Literal["interrupted"] = "interrupted"
    pending: tuple[PendingToolApproval, ...]
    checkpoint: dict[str, Any]
    usage: ModelUsageObservation = Field(default_factory=ModelUsageObservation)


BackendOutcome = AgentRunCompleted | AgentRunInterrupted
