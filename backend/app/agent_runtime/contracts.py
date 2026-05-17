from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from backend.app.agents.models import AgentProfile


@dataclass(frozen=True)
class AgentRuntimeContext:
    workspace_id: UUID
    task_id: UUID | None
    run_id: UUID
    user_id: UUID | None = None
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunRequest:
    agent_profile: AgentProfile
    input_text: str
    context: AgentRuntimeContext
    max_turns: int = 10


@dataclass(frozen=True)
class AgentRunResult:
    final_output: str
    raw_output: object | None = None


class AgentRunner(Protocol):
    async def run(self, request: AgentRunRequest) -> AgentRunResult: ...

