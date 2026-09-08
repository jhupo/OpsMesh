import json
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from agents import (
    Agent,
    RunConfig,
    RunContextWrapper,
    Runner,
    RunState,
)
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
)
from agents.handoffs import HandoffInputData
from agents.handoffs import handoff as sdk_handoff
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider

from backend.app.agent_runtime.base import BaseSDKAgentRuntimeAdapter
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentRef,
    AgentRuntimeAgentTool,
    AgentRuntimeAgentToolResult,
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeEvent,
    AgentRuntimeGuardrailResult,
)
from backend.app.agent_runtime.errors import (
    AgentRuntimeGuardrailBlockedError,
    AgentRuntimePolicyError,
    normalize_agent_error,
)
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver
from backend.app.agent_runtime.guardrails import (
    guardrail_events,
    validated_structured_output,
)
from backend.app.agent_runtime.openai_guardrails import (
    OpenAIRuntimeOutputSchema,
    OpenAIRuntimeOutputSchemaError,
    merged_openai_guardrail_results,
    openai_input_guardrails,
    openai_output_guardrails,
)
from backend.app.agent_runtime.openai_lifecycle import OpenAIRuntimeHooks
from backend.app.agent_runtime.openai_results import OpenAIAgentsResultMapper, jsonable
from backend.app.agent_runtime.openai_settings import OpenAIModelSettingsMapper
from backend.app.agent_runtime.openai_streaming import run_openai_streamed
from backend.app.agent_runtime.openai_tools import OpenAIToolBridge
from backend.app.agent_runtime.usage import runtime_usage
from backend.app.core.resilience import CircuitBreakerConfig
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import (
    is_openai_compatible_provider,
    model_provider_key,
)

DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()


class OpenAIAgentsRunner(BaseSDKAgentRuntimeAdapter):
    capabilities = AgentRuntimeCapabilities(
        provider="openai-compatible",
        adapter="openai_agents",
        supported=frozenset(
            {
                AgentRuntimeCapability.TOOLS,
                AgentRuntimeCapability.HANDOFFS,
                AgentRuntimeCapability.AGENTS_AS_TOOLS,
                AgentRuntimeCapability.STRUCTURED_OUTPUT,
                AgentRuntimeCapability.STREAMING,
                AgentRuntimeCapability.RESUMABLE_STATE,
                AgentRuntimeCapability.GUARDRAILS,
                AgentRuntimeCapability.SESSIONS,
                AgentRuntimeCapability.CANCELLATION,
                AgentRuntimeCapability.LIFECYCLE_EVENTS,
                AgentRuntimeCapability.USAGE,
            }
        ),
        limits={"max_agent_tool_depth": 3, "max_agent_tool_turns": 20},
        unsupported_reasons={},
    )

    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
    ) -> None:
        super().__init__(max_attempts=max_attempts, circuit_config=circuit_config)
        self._settings_mapper = OpenAIModelSettingsMapper()
        self._tool_bridge = OpenAIToolBridge()
        self._result_mapper = OpenAIAgentsResultMapper()

    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult:
        handoff_audits: dict[str, dict[str, object]] = {}
        agent_tool_calls: list[AgentRuntimeAgentToolResult] = []
        guardrail_results: list[AgentRuntimeGuardrailResult] = []
        agent = self._build_agent(
            request,
            handoff_audits=handoff_audits,
            agent_tool_calls=agent_tool_calls,
            guardrail_results=guardrail_results,
        )
        runner_input = await self._runner_input(request, agent)

        hooks = OpenAIRuntimeHooks(observer, request.cancellation)

        async def invoke_sdk() -> Any:
            try:
                if request.stream or request.cancellation is not None:
                    return await run_openai_streamed(
                        request=request,
                        agent=agent,
                        runner_input=runner_input,
                        hooks=hooks,
                        run_config=self._run_config(request),
                        observer=observer,
                    )
                return await Runner.run(
                    agent,
                    runner_input,
                    context=request.context,
                    max_turns=request.max_turns,
                    hooks=hooks,
                    run_config=self._run_config(request),
                    previous_response_id=request.previous_response_id,
                    conversation_id=request.conversation_id,
                    session=request.session,
                )
            except OpenAIRuntimeOutputSchemaError as exc:
                raise exc.policy_error from exc
            except (
                InputGuardrailTripwireTriggered,
                OutputGuardrailTripwireTriggered,
            ) as exc:
                raise _guardrail_blocked_error(exc, guardrail_results) from exc

        result = await invoke_sdk()
        guardrail_results = merged_openai_guardrail_results(result, guardrail_results)
        interruptions = self._result_mapper.interruptions(result)
        final_output, structured_output = self._result_mapper.final_output(result)
        if request.output_schema is not None and not interruptions:
            structured_output = validated_structured_output(
                request.output_schema,
                getattr(result, "final_output", None),
            )
            final_output = json.dumps(
                structured_output.value,
                ensure_ascii=False,
                sort_keys=True,
            )
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
        runtime_events.extend(_agent_tool_events(agent_tool_calls))
        runtime_events.extend(guardrail_events(guardrail_results))
        return AgentRunResult(
            final_output=final_output,
            raw_output=self._result_mapper.safe_raw_output(result),
            events=tuple(runtime_events),
            resume_state=self._result_mapper.resume_state(result),
            interruptions=tuple(interruptions),
            structured_output=structured_output,
            stream_events=(),
            handoffs=tuple(handoffs),
            agent_tool_calls=tuple(agent_tool_calls),
            guardrail_results=tuple(guardrail_results),
            usage=runtime_usage(getattr(result, "usage", None)),
        )

    def _validate_request(self, request: AgentRunRequest) -> None:
        self._validate_agent_tools(request)

    def _provider_circuit_key(self, request: AgentRunRequest) -> str:
        return _model_provider_circuit_key(request)

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
        agent_tool_calls: list[AgentRuntimeAgentToolResult] | None = None,
        guardrail_results: list[AgentRuntimeGuardrailResult] | None = None,
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
        nested_calls = agent_tool_calls if agent_tool_calls is not None else []
        runtime_guardrail_results = (
            guardrail_results if guardrail_results is not None else []
        )
        tools = self._tool_bridge.tools(request)
        tools.extend(
            self._build_agent_tools(
                request,
                request.agent_tools,
                source=AgentRuntimeAgentRef(
                    name=profile.name,
                    profile_id=getattr(profile, "id", None),
                    role=profile.role,
                ),
                calls=nested_calls,
                guardrail_results=runtime_guardrail_results,
            )
        )
        input_guardrails, output_guardrails = _openai_guardrails(
            request,
            runtime_guardrail_results,
        )
        return Agent(
            name=profile.name,
            instructions=profile.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(profile.model_settings),
            tools=tools,
            handoffs=self._build_handoffs(
                request,
                handoff_audits if handoff_audits is not None else {},
                guardrail_results=runtime_guardrail_results,
            ),
            output_type=(
                OpenAIRuntimeOutputSchema(request.output_schema)
                if request.output_schema is not None
                else None
            ),
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
        )

    def _build_agent_tools(
        self,
        request: AgentRunRequest,
        definitions: tuple[AgentRuntimeAgentTool, ...],
        *,
        source: AgentRuntimeAgentRef,
        calls: list[AgentRuntimeAgentToolResult],
        guardrail_results: list[AgentRuntimeGuardrailResult],
    ) -> list[Any]:
        tools: list[Any] = []
        for definition in definitions:
            target = self._build_agent_tool_target(
                request,
                definition,
                calls=calls,
                guardrail_results=guardrail_results,
            )

            async def extract_output(
                result: Any,
                *,
                item: AgentRuntimeAgentTool = definition,
                source_ref: AgentRuntimeAgentRef = source,
            ) -> str:
                invocation = getattr(result, "agent_tool_invocation", None)
                status = (
                    "waiting_approval"
                    if getattr(result, "interruptions", None)
                    else "completed"
                )
                calls.append(
                    _agent_tool_result(
                        source=source_ref,
                        item=item,
                        invocation=invocation,
                        status=status,
                        usage=getattr(result, "usage", None),
                    )
                )
                output, _ = self._result_mapper.final_output(result)
                return output

            async def handle_failure(
                context: Any,
                exc: Exception,
                *,
                item: AgentRuntimeAgentTool = definition,
                source_ref: AgentRuntimeAgentRef = source,
            ) -> str:
                if isinstance(exc, AgentRuntimePolicyError):
                    raise exc
                if isinstance(
                    exc,
                    InputGuardrailTripwireTriggered | OutputGuardrailTripwireTriggered,
                ):
                    raise _guardrail_blocked_error(exc, guardrail_results) from exc
                error = normalize_agent_error(exc)
                calls.append(
                    _agent_tool_result(
                        source=source_ref,
                        item=item,
                        invocation=context,
                        status="failed",
                        error=error.as_dict(),
                    )
                )
                return f"Specialist agent failed: {error.message}"

            tools.append(
                target.as_tool(
                    tool_name=definition.tool_name,
                    tool_description=definition.description,
                    max_turns=definition.max_turns,
                    custom_output_extractor=extract_output,
                    failure_error_function=handle_failure,
                )
            )
        return tools

    def _build_agent_tool_target(
        self,
        request: AgentRunRequest,
        definition: AgentRuntimeAgentTool,
        *,
        calls: list[AgentRuntimeAgentToolResult],
        guardrail_results: list[AgentRuntimeGuardrailResult],
    ) -> Agent[Any]:
        if not is_openai_compatible_provider(definition.provider):
            raise ValueError("OpenAI agent tools require an OpenAI-compatible target provider")
        if not definition.api_key:
            raise ValueError("OpenAI agent tool target requires an explicit provider API key")
        model = OpenAIProvider(
            api_key=definition.api_key,
            base_url=normalize_openai_compatible_base_url(definition.base_url),
            use_responses=_use_responses_api(definition.model_api),
        ).get_model(definition.model)
        scoped_request = replace(
            request,
            context=definition.context,
            model=definition.model,
            provider=definition.provider,
            base_url=definition.base_url,
            api_key=definition.api_key,
            model_api=definition.model_api,
            model_provider_credential_id=definition.model_provider_credential_id,
            agent_tools=definition.nested_tools,
            handoffs=(),
            handoff_agents=(),
        )
        tools = self._tool_bridge.tools(scoped_request)
        tools.extend(
            self._build_agent_tools(
                scoped_request,
                definition.nested_tools,
                source=definition.target.ref,
                calls=calls,
                guardrail_results=guardrail_results,
            )
        )
        input_guardrails, output_guardrails = _openai_guardrails(
            scoped_request,
            guardrail_results,
        )
        return Agent(
            name=definition.target.ref.name,
            handoff_description=definition.target.handoff_description,
            instructions=definition.target.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(
                definition.target.model_settings
            ),
            tools=tools,
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
        )

    def _validate_agent_tools(self, request: AgentRunRequest) -> None:
        self._validate_agent_tool_level(
            request.agent_tools,
            workspace_id=request.context.workspace_id,
            task_id=request.context.task_id,
            run_id=request.context.run_id,
            expected_depth=1,
            product_tool_names=set(request.context.allowed_tools),
            path=(getattr(request.agent_profile, "id", None),),
        )

    def _validate_agent_tool_level(
        self,
        definitions: tuple[AgentRuntimeAgentTool, ...],
        *,
        workspace_id: object,
        task_id: object,
        run_id: object,
        expected_depth: int,
        product_tool_names: set[str],
        path: tuple[object, ...],
    ) -> None:
        names: set[str] = set()
        for definition in definitions:
            profile_id = definition.target.ref.profile_id
            if definition.tool_name in names or definition.tool_name in product_tool_names:
                raise ValueError("Agent tool names must be unique and cannot shadow runtime tools")
            names.add(definition.tool_name)
            if definition.target.workspace_id != workspace_id:
                raise ValueError("Agent tool target belongs to another workspace")
            if (
                definition.context.workspace_id != workspace_id
                or definition.context.task_id != task_id
                or definition.context.run_id != run_id
            ):
                raise ValueError("Agent tool context does not match the parent run")
            if definition.depth != expected_depth:
                raise ValueError("Agent tool depth does not match its nesting level")
            if not 1 <= definition.depth <= definition.max_depth <= 3:
                raise ValueError("Agent tool depth policy is invalid")
            if not 1 <= definition.max_turns <= 20:
                raise ValueError("Agent tool turn limit is invalid")
            if profile_id is None or profile_id in path:
                raise ValueError("Agent tool graph contains a missing target or cycle")
            if definition.nested_tools and definition.depth >= definition.max_depth:
                raise ValueError("Agent tool graph exceeds its maximum depth")
            self._validate_agent_tool_level(
                definition.nested_tools,
                workspace_id=workspace_id,
                task_id=task_id,
                run_id=run_id,
                expected_depth=expected_depth + 1,
                product_tool_names=set(definition.context.allowed_tools),
                path=(*path, profile_id),
            )

    def _build_handoffs(
        self,
        request: AgentRunRequest,
        handoff_audits: dict[str, dict[str, object]],
        *,
        guardrail_results: list[AgentRuntimeGuardrailResult],
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
            target = self._build_handoff_agent(
                request,
                definition,
                guardrail_results=guardrail_results,
            )
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
        *,
        guardrail_results: list[AgentRuntimeGuardrailResult],
    ) -> Agent[Any]:
        model_name = definition.model or request.model or request.agent_profile.model
        if request.api_key is None:
            raise ValueError("OpenAI-compatible runtime requires an explicit provider API key")
        model: str | Model = OpenAIProvider(
            api_key=request.api_key,
            base_url=normalize_openai_compatible_base_url(request.base_url),
            use_responses=_use_responses_api(request.model_api),
        ).get_model(model_name)
        input_guardrails, output_guardrails = _openai_guardrails(
            request,
            guardrail_results,
        )
        return Agent(
            name=definition.ref.name,
            handoff_description=definition.handoff_description,
            instructions=definition.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(definition.model_settings),
            tools=self._tool_bridge.tools(request),
            output_type=(
                OpenAIRuntimeOutputSchema(request.output_schema)
                if request.output_schema is not None
                else None
            ),
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
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


def _openai_guardrails(
    request: AgentRunRequest,
    results: list[AgentRuntimeGuardrailResult],
) -> tuple[list[Any], list[Any]]:
    if request.guardrails is None:
        return [], []
    return (
        openai_input_guardrails(request.guardrails.input, results),
        openai_output_guardrails(request.guardrails.output, results),
    )


def _guardrail_blocked_error(
    exc: InputGuardrailTripwireTriggered | OutputGuardrailTripwireTriggered,
    results: list[AgentRuntimeGuardrailResult],
) -> AgentRuntimeGuardrailBlockedError:
    result = next((item for item in reversed(results) if item.status == "blocked"), None)
    if result is None:
        raise RuntimeError("OpenAI SDK guardrail result lost its OpsMesh provenance") from exc
    return AgentRuntimeGuardrailBlockedError(result)


def _agent_tool_result(
    *,
    source: AgentRuntimeAgentRef,
    item: AgentRuntimeAgentTool,
    invocation: object | None,
    status: str,
    usage: object | None = None,
    error: dict[str, object] | None = None,
) -> AgentRuntimeAgentToolResult:
    call_id = getattr(invocation, "tool_call_id", None)
    if not isinstance(call_id, str) or not call_id:
        call_id = "unknown"
    usage_payload = jsonable(usage)
    return AgentRuntimeAgentToolResult(
        source=source,
        target=item.target.ref,
        tool_name=item.tool_name,
        tool_call_id=call_id,
        status=status,
        depth=item.depth,
        max_turns=item.max_turns,
        usage=usage_payload if isinstance(usage_payload, dict) else {},
        error=error,
    )


def _agent_tool_events(
    calls: list[AgentRuntimeAgentToolResult],
) -> list[AgentRuntimeEvent]:
    return [
        AgentRuntimeEvent(
            event_type=f"agent.tool.{call.status}",
            message=(
                "Specialist agent completed delegated work."
                if call.status == "completed"
                else "Specialist agent delegation changed state."
            ),
            payload={
                "source_agent": call.source.name,
                "source_agent_profile_id": str(call.source.profile_id)
                if call.source.profile_id is not None
                else None,
                "target_agent": call.target.name,
                "target_agent_profile_id": str(call.target.profile_id)
                if call.target.profile_id is not None
                else None,
                "tool_name": call.tool_name,
                "tool_call_id": call.tool_call_id,
                "status": call.status,
                "depth": call.depth,
                "max_turns": call.max_turns,
                "usage": call.usage,
                "error": call.error,
            },
        )
        for call in calls
    ]



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
