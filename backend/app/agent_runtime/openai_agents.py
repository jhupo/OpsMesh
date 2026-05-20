from typing import Any, Literal

from agents import Agent, ModelSettings, Runner, function_tool
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeToolExecutor,
)


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
            raw_output=self._safe_raw_output(result),
            events=tuple(self._runtime_events(result)),
        )

    def _build_agent(self, request: AgentRunRequest) -> Agent[Any]:
        profile = request.agent_profile
        model_name = request.model or profile.model
        model: str | Model = model_name
        if request.api_key is not None or request.base_url is not None:
            model = OpenAIProvider(
                api_key=request.api_key,
                base_url=request.base_url,
            ).get_model(model_name)
        return Agent(
            name=profile.name,
            instructions=profile.instructions,
            model=model,
            model_settings=self._model_settings(profile.model_settings),
            tools=self._tools(request),
        )

    def _tools(self, request: AgentRunRequest) -> list[Any]:
        if request.tool_executor is None:
            return []
        return [
            self._mcp_function_tool(tool_name, request.tool_executor)
            for tool_name in request.context.allowed_tools
        ]

    def _mcp_function_tool(
        self,
        tool_name: str,
        executor: AgentRuntimeToolExecutor,
    ) -> Any:
        async def call_mcp_tool(ctx: Any, arguments: dict[str, object]) -> dict[str, object]:
            result = executor.execute_tool(
                context=ctx.context,
                tool_name=tool_name,
                arguments=arguments,
            )
            if result.status == "completed":
                return result.output or {}
            return {
                "error": result.error
                or {
                    "code": "mcp_tool_failed",
                    "message": "MCP tool failed",
                }
            }

        call_mcp_tool.__name__ = f"mcp_{_safe_tool_function_name(tool_name)}"
        call_mcp_tool.__doc__ = (
            "Execute an approved MCP tool. "
            "Pass a JSON object with the arguments required by the tool."
        )
        return function_tool(
            call_mcp_tool,
            name_override=tool_name,
            description_override=f"Execute the approved MCP tool `{tool_name}`.",
            strict_mode=False,
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

    def _safe_raw_output(self, result: Any) -> dict[str, object]:
        payload: dict[str, object] = {"final_output": str(getattr(result, "final_output", ""))}
        last_agent = getattr(result, "last_agent", None)
        if last_agent is not None:
            payload["last_agent"] = str(getattr(last_agent, "name", last_agent))
        usage = getattr(result, "usage", None)
        if usage is not None:
            payload["usage"] = self._jsonable(usage)
        return payload

    def _runtime_events(self, result: Any) -> list[AgentRuntimeEvent]:
        events: list[AgentRuntimeEvent] = []
        last_agent = getattr(result, "last_agent", None)
        if last_agent is not None:
            events.append(
                AgentRuntimeEvent(
                    event_type="agent.handoff",
                    message="Run finished with agent handoff state.",
                    payload={"target_agent": str(getattr(last_agent, "name", last_agent))},
                )
            )
        usage = getattr(result, "usage", None)
        if usage is not None:
            events.append(
                AgentRuntimeEvent(
                    event_type="model.usage",
                    message="Model usage recorded.",
                    payload={"usage": self._jsonable(usage)},
                )
            )
        raw_events = getattr(result, "events", None)
        if isinstance(raw_events, list | tuple):
            for item in raw_events:
                event = self._runtime_event_from_sdk_item(item)
                if event is not None:
                    events.append(event)
        return events

    def _runtime_event_from_sdk_item(self, item: object) -> AgentRuntimeEvent | None:
        event_type = getattr(item, "type", None) or getattr(item, "event_type", None)
        if not isinstance(event_type, str) or not event_type:
            return None
        payload = self._jsonable(item)
        return AgentRuntimeEvent(
            event_type=event_type,
            message=str(getattr(item, "message", "") or event_type),
            payload=payload if isinstance(payload, dict) else {"value": payload},
        )

    def _jsonable(self, value: Any) -> object:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._jsonable(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                return self._jsonable(dumped)
        return str(value)


def _safe_tool_function_name(tool_name: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in tool_name)
    return safe or "tool"
