from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.core.config import Settings


def build_agent_runner(settings: Settings) -> AgentRunner:
    match settings.agent_runner_backend:
        case "fake":
            return FakeAgentRunner()
        case "openai":
            return OpenAIAgentsRunner()
