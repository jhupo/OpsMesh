from typing import Any

from agents import Agent, Runner

from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult


class OpenAIAgentsRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        agent = self._build_agent(request)
        result = await Runner.run(
            agent,
            request.input_text,
            context=request.context,
            max_turns=request.max_turns,
        )
        return AgentRunResult(
            final_output=str(result.final_output),
            raw_output=result,
        )

    def _build_agent(self, request: AgentRunRequest) -> Agent[Any]:
        profile = request.agent_profile
        return Agent(
            name=profile.name,
            instructions=profile.instructions,
            model=profile.model,
        )

