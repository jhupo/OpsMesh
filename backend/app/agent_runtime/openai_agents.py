import json
from typing import Any
from urllib.parse import urlparse

from agents import (
    Agent,
    RunConfig,
    RunContextWrapper,
    Runner,
    RunState,
)
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.openai_results import OpenAIAgentsResultMapper
from backend.app.agent_runtime.openai_settings import OpenAIModelSettingsMapper
from backend.app.agent_runtime.openai_tools import OpenAIToolBridge
from backend.app.core.resilience import CircuitBreakerConfig, async_retry_with_circuit
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import model_provider_key

DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()


class OpenAIAgentsRunner:
    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._circuit_config = circuit_config
        self._settings_mapper = OpenAIModelSettingsMapper()
        self._tool_bridge = OpenAIToolBridge()
        self._result_mapper = OpenAIAgentsResultMapper()

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        agent = self._build_agent(request)
        runner_input = await self._runner_input(request, agent)
        result = await async_retry_with_circuit(
            key=_model_provider_circuit_key(request),
            func=lambda: Runner.run(
                agent,
                runner_input,
                context=request.context,
                max_turns=request.max_turns,
                run_config=self._run_config(request),
                previous_response_id=request.previous_response_id,
                conversation_id=request.conversation_id,
                session=request.session,
            ),
            max_attempts=self._max_attempts,
            circuit_config=self._circuit_config,
            should_retry=lambda exc: normalize_agent_error(exc).retryable,
        )
        return AgentRunResult(
            final_output=str(result.final_output) if result.final_output is not None else "",
            raw_output=self._result_mapper.safe_raw_output(result),
            events=tuple(self._result_mapper.runtime_events(result)),
            resume_state=self._result_mapper.resume_state(result),
            interruptions=tuple(self._result_mapper.interruptions(result)),
        )

    async def _runner_input(
        self,
        request: AgentRunRequest,
        agent: Agent[Any],
    ) -> str | list[Any] | RunState[Any]:
        if request.resume_state is None:
            return self._input_for_request(request)
        if request.resume_state.provider != "openai_agents":
            raise ValueError("OpenAI Agents runner cannot restore another provider's state")
        try:
            state_payload = json.loads(request.resume_state.serialized_state)
        except json.JSONDecodeError as exc:
            raise ValueError("Stored OpenAI Agents run state is not valid JSON") from exc
        if not isinstance(state_payload, dict):
            raise ValueError("Stored OpenAI Agents run state must be a JSON object")
        state = await RunState.from_json(
            initial_agent=agent,
            state_json=state_payload,
            context_override=RunContextWrapper(context=request.context),
            strict_context=True,
        )
        if not request.approval_decisions:
            return state
        interruptions = {
            (item.call_id, item.name): item
            for item in state.get_interruptions()
            if item.call_id is not None and item.name is not None
        }
        for decision in request.approval_decisions:
            interruption = interruptions.get((decision.tool_call_id, decision.tool_name))
            if interruption is None:
                raise ValueError("Stored approval decision does not match the SDK interruption")
            if decision.status == "approved":
                state.approve(interruption)
            elif decision.status == "rejected":
                state.reject(interruption, rejection_message=decision.reason)
            else:
                raise ValueError("Stored tool approval decision is invalid")
        return state

    def _build_agent(self, request: AgentRunRequest) -> Agent[Any]:
        profile = request.agent_profile
        model_name = request.model or profile.model
        if request.api_key is None:
            raise ValueError("OpenAI-compatible runtime requires an explicit provider API key")
        model: str | Model = OpenAIProvider(
            api_key=request.api_key,
            base_url=normalize_openai_compatible_base_url(request.base_url),
            use_responses=_use_responses_api(request.model_api),
        ).get_model(model_name)
        return Agent(
            name=profile.name,
            instructions=profile.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(profile.model_settings),
            tools=self._tool_bridge.tools(request),
        )

    def _input_for_request(self, request: AgentRunRequest) -> str:
        if not request.continuations:
            return request.input_text
        continuation_lines = [
            "- "
            + json.dumps(
                {
                    "tool_name": continuation.tool_name,
                    "status": continuation.status,
                    "result": continuation.result
                    if continuation.status == "completed"
                    else continuation.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for continuation in request.continuations
        ]
        return (
            request.input_text
            + "\n\nCompleted runtime tool results:\n"
            + "\n".join(continuation_lines)
        )

    def _run_config(self, request: AgentRunRequest) -> RunConfig | None:
        if request.tracing is None:
            return None
        return RunConfig(
            workflow_name=request.tracing.workflow_name,
            trace_id=request.tracing.trace_id,
            group_id=request.tracing.group_id,
            trace_metadata=request.tracing.metadata,
            tracing_disabled=request.tracing.disabled,
            trace_include_sensitive_data=request.tracing.include_sensitive_data,
        )



def _model_provider_circuit_key(request: AgentRunRequest) -> str:
    provider = model_provider_key(request.provider) or "openai"
    base_url = normalize_openai_compatible_base_url(request.base_url) or "openai-default"
    try:
        parsed = urlparse(base_url)
    except ValueError:
        host = "invalid-url"
    else:
        host = parsed.netloc or parsed.path or "openai-default"
    credential = (
        str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else "no-credential"
    )
    model_api = canonical_model_api(request.model_api) or "unspecified"
    return (
        f"model-provider:{provider}:{host}:{credential}:{model_api}:"
        f"{request.model or request.agent_profile.model}"
    )


def _use_responses_api(model_api: str | None) -> bool | None:
    normalized = canonical_model_api(model_api)
    if normalized == OPENAI_CHAT_COMPLETIONS_API:
        return False
    if normalized == OPENAI_RESPONSES_API:
        return True
    return None
