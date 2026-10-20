"""Native Runner context reused across durable approval checkpoints."""

import asyncio
from typing import Any

from agents import RunConfig, Runner, RunResult

from opsmesh.orchestration.execution.agents import build_agents
from opsmesh.orchestration.execution.contracts import PreparedRun
from opsmesh.orchestration.execution.session import TemporalSession


class AgentRunner:
    """Own SDK objects only; the caller owns scheduling, timeout and approval decisions."""

    def __init__(self, prepared: PreparedRun, pending_tools: set[asyncio.Task[Any]]) -> None:
        self.agent = build_agents(prepared, pending_tools)
        self.session = TemporalSession(
            principal_id=prepared.principal_id,
            session_id=prepared.request.session_id,
            run_id=prepared.reference.run_id,
        )
        self.max_turns = prepared.request.limits.max_turns

    async def run(self, current_input: Any) -> RunResult:
        return await Runner.run(
            self.agent,
            input=current_input,
            session=self.session,
            max_turns=self.max_turns,
            run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
        )
