"""Install offline models through the SDK's public ModelProvider extension."""

from dataclasses import replace

from agents import RunConfig

from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor


def install_model_provider(monkeypatch, provider):
    original = OpenAIAgentExecutor._run_config

    def configured(self, request):
        config = original(self, request) or RunConfig()
        return replace(config, model_provider=provider)

    monkeypatch.setattr(OpenAIAgentExecutor, "_run_config", configured)
