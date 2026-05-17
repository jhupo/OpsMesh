from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult


class FakeAgentRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(final_output="fake_run_completed")

