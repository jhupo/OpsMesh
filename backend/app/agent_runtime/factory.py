from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner


def build_agent_runner() -> AgentRunner:
    return OpenAIAgentsRunner()
