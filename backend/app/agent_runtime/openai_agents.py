from typing import Any, Literal

from agents import Agent, ModelSettings, Runner

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
            model_settings=self._model_settings(profile.model_settings),
        )

    def _model_settings(self, settings: dict[str, object]) -> ModelSettings:
        temperature = self._float_setting(settings, "temperature")
        top_p = self._float_setting(settings, "top_p")
        frequency_penalty = self._float_setting(settings, "frequency_penalty")
        presence_penalty = self._float_setting(settings, "presence_penalty")
        max_tokens = self._int_setting(settings, "max_tokens")
        parallel_tool_calls = self._bool_setting(settings, "parallel_tool_calls")
        store = self._bool_setting(settings, "store")
        include_usage = self._bool_setting(settings, "include_usage")
        truncation = settings.get("truncation")
        safe_truncation: Literal["auto", "disabled"] | None = None
        if truncation == "auto" or truncation == "disabled":
            safe_truncation = truncation
        verbosity = settings.get("verbosity")
        safe_verbosity: Literal["low", "medium", "high"] | None = None
        if verbosity == "low" or verbosity == "medium" or verbosity == "high":
            safe_verbosity = verbosity
        metadata = settings.get("metadata")
        safe_metadata: dict[str, str] | None = None
        if isinstance(metadata, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            safe_metadata = {str(key): str(value) for key, value in metadata.items()}
        return ModelSettings(
            temperature=temperature,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            parallel_tool_calls=parallel_tool_calls,
            truncation=safe_truncation,
            verbosity=safe_verbosity,
            metadata=safe_metadata,
            store=store,
            include_usage=include_usage,
        )

    def _float_setting(self, settings: dict[str, object], key: str) -> float | None:
        value = settings.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return None

    def _int_setting(self, settings: dict[str, object], key: str) -> int | None:
        value = settings.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _bool_setting(self, settings: dict[str, object], key: str) -> bool | None:
        value = settings.get(key)
        if isinstance(value, bool):
            return value
        return None
