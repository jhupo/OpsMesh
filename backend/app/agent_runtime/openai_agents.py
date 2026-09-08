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
from agents.handoffs import HandoffInputData
from agents.handoffs import handoff as sdk_handoff
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAgentDefinition,
    AgentRuntimeEvent,
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
        self._validate_contract_requests(request)
        handoff_audits: dict[str, dict[str, object]] = {}
        agent = self._build_agent(request, handoff_audits=handoff_audits)
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
        final_output, structured_output = self._result_mapper.final_output(result)
        handoffs = self._result_mapper.handoffs(result, handoff_audits)
        runtime_events = self._result_mapper.runtime_events(result)
        runtime_events.extend(
            AgentRuntimeEvent(
                event_type="agent.handoff",
                message="Agent handoff completed.",
                payload={
                    "source_agent": handoff.source.name,
                    "target_agent": handoff.target.name,
                    "status": handoff.status,
                    "filtered_context_keys": list(handoff.filtered_context_keys),
                    "metadata": handoff.metadata,
                },
            )
            for handoff in handoffs
        )
        return AgentRunResult(
            final_output=final_output,
            raw_output=self._result_mapper.safe_raw_output(result),
            events=tuple(runtime_events),
            resume_state=self._result_mapper.resume_state(result),
            interruptions=tuple(self._result_mapper.interruptions(result)),
            structured_output=structured_output,
            stream_events=tuple(self._result_mapper.stream_events(result)),
            handoffs=tuple(handoffs),
        )

    def _validate_contract_requests(self, request: AgentRunRequest) -> None:
        unsupported: list[str] = []
        if request.output_schema is not None:
            unsupported.append("structured output")
        if request.guardrails is not None:
            unsupported.append("guardrails")
        if request.stream:
            unsupported.append("streaming")
        if unsupported:
            raise NotImplementedError(
                "OpenAI Agents runtime contract features are not enabled yet: "
                + ", ".join(unsupported)
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

    def _build_agent(
        self,
        request: AgentRunRequest,
        *,
        handoff_audits: dict[str, dict[str, object]] | None = None,
    ) -> Agent[Any]:
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
            handoffs=self._build_handoffs(
                request,
                handoff_audits if handoff_audits is not None else {},
            ),
        )

    def _build_handoffs(
        self,
        request: AgentRunRequest,
        handoff_audits: dict[str, dict[str, object]],
    ) -> list[Any]:
        if not request.handoffs:
            return []
        definitions: dict[tuple[str, str], AgentRuntimeAgentDefinition] = {}
        for definition in request.handoff_agents:
            for key in {
                _agent_definition_key(definition),
                ("name", definition.ref.name),
            }:
                if key in definitions and definitions[key] != definition:
                    raise ValueError("OpenAI Agents handoff targets must be unique")
                definitions[key] = definition
        handoffs: list[Any] = []
        seen_targets: set[tuple[str, str]] = set()
        for descriptor in request.handoffs:
            target_key = _handoff_target_key(descriptor.target)
            if target_key in seen_targets:
                raise ValueError("OpenAI Agents handoff targets must be unique")
            seen_targets.add(target_key)
            definition = definitions.get(target_key)
            if definition is None:
                raise ValueError(
                    f"Handoff target {descriptor.target.name} is not in the authorized target set"
                )
            if definition.workspace_id != request.context.workspace_id:
                raise ValueError("Handoff target belongs to another workspace")
            target = self._build_handoff_agent(request, definition)
            audit = handoff_audits.setdefault(target.name, {})
            source_profile_id = getattr(request.agent_profile, "id", None)
            if source_profile_id is not None:
                audit["source_agent_profile_id"] = str(source_profile_id)
            if definition.ref.profile_id is not None:
                audit["target_agent_profile_id"] = str(definition.ref.profile_id)
            handoffs.append(
                sdk_handoff(
                    target,
                    tool_description_override=descriptor.reason
                    or f"Transfer the request to {target.name}.",
                    input_filter=_handoff_input_filter(descriptor.input_filter, audit),
                )
            )
        return handoffs

    def _build_handoff_agent(
        self,
        request: AgentRunRequest,
        definition: AgentRuntimeAgentDefinition,
    ) -> Agent[Any]:
        model_name = definition.model or request.model or request.agent_profile.model
        if request.api_key is None:
            raise ValueError("OpenAI-compatible runtime requires an explicit provider API key")
        model: str | Model = OpenAIProvider(
            api_key=request.api_key,
            base_url=normalize_openai_compatible_base_url(request.base_url),
            use_responses=_use_responses_api(request.model_api),
        ).get_model(model_name)
        return Agent(
            name=definition.ref.name,
            handoff_description=definition.handoff_description,
            instructions=definition.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(definition.model_settings),
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


def _handoff_target_key(ref: object) -> tuple[str, str]:
    profile_id = getattr(ref, "profile_id", None)
    if profile_id is not None:
        return ("profile_id", str(profile_id))
    return ("name", str(getattr(ref, "name", "")))


def _agent_definition_key(definition: AgentRuntimeAgentDefinition) -> tuple[str, str]:
    return _handoff_target_key(definition.ref)


def _handoff_input_filter(
    allowed_item_types: tuple[str, ...],
    audit: dict[str, object],
) -> Any:
    if not allowed_item_types:
        return None
    allowed = frozenset(allowed_item_types)

    def _filter(data: HandoffInputData) -> HandoffInputData:
        source = data.input_items if data.input_items is not None else data.new_items
        retained = []
        removed: set[str] = set()
        for item in source:
            item_type = _handoff_item_type(item)
            if item_type in allowed:
                retained.append(item)
            else:
                removed.add(item_type)
        audit["input_items_before"] = len(source)
        audit["input_items_after"] = len(retained)
        audit["filtered_context_keys"] = tuple(sorted(removed))
        return data.clone(input_items=tuple(retained))

    return _filter


def _handoff_item_type(item: object) -> str:
    item_type = getattr(item, "type", None)
    if isinstance(item_type, str) and item_type:
        return item_type
    raw_item = getattr(item, "raw_item", None)
    if isinstance(raw_item, dict):
        raw_type = raw_item.get("type") or raw_item.get("role")
        if isinstance(raw_type, str) and raw_type:
            return raw_type
    if isinstance(item, dict):
        raw_type = item.get("type") or item.get("role")
        if isinstance(raw_type, str) and raw_type:
            return raw_type
    return type(item).__name__
