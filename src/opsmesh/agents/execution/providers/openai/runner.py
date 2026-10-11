import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from agents import (
    Agent,
    RunConfig,
    RunContextWrapper,
    Runner,
    RunResult,
    RunResultStreaming,
    RunState,
    Session,
    ToolExecutionConfig,
)
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    OutputGuardrailTripwireTriggered,
    UserError,
)
from agents.handoffs import handoff as sdk_handoff
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider
from agents.usage import Usage
from openai import APIConnectionError, APITimeoutError, OpenAIError
from pydantic import TypeAdapter

from opsmesh.agents.execution.base import BaseSDKAgentRuntimeAdapter
from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAgentRef,
    AgentRuntimeAgentTarget,
    AgentRuntimeAgentToolResult,
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeContext,
    AgentRuntimeEvent,
    AgentRuntimeGuardrailResult,
    AgentRuntimeModelUsage,
    AgentRuntimeProfile,
    AgentRuntimeStructuredOutput,
)
from opsmesh.agents.execution.errors import (
    AgentRuntimeGuardrailBlockedError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
    normalize_agent_error,
)
from opsmesh.agents.execution.guardrails import guardrail_events
from opsmesh.agents.execution.observer import AgentRuntimeExecutionObserver
from opsmesh.agents.execution.providers.openai.guardrails import (
    OpenAIRuntimeOutputSchema,
    OpenAIRuntimeOutputSchemaError,
    merged_openai_guardrail_results,
    openai_input_guardrails,
    openai_output_guardrails,
)
from opsmesh.agents.execution.providers.openai.lifecycle import OpenAIRuntimeHooks
from opsmesh.agents.execution.providers.openai.mcp import (
    GovernedAgent,
    GovernedSandboxAgent,
    runtime_mcp_servers,
)
from opsmesh.agents.execution.providers.openai.memory_usage import memory_usage
from opsmesh.agents.execution.providers.openai.model_errors import GraphModel, graph_model_error
from opsmesh.agents.execution.providers.openai.model_usage import model_usages
from opsmesh.agents.execution.providers.openai.results import OpenAIAgentsResultMapper, jsonable
from opsmesh.agents.execution.providers.openai.sandbox import (
    sandbox_capabilities,
    sandbox_run_config,
)
from opsmesh.agents.execution.providers.openai.settings import OpenAIModelSettingsMapper
from opsmesh.agents.execution.providers.openai.streaming import run_openai_streamed
from opsmesh.agents.execution.providers.openai.tools import OpenAIToolBridge
from opsmesh.agents.execution.usage import runtime_usage
from opsmesh.agents.providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from opsmesh.agents.providers.policy import (
    is_openai_compatible_provider,
)
from opsmesh.resources.memory.policy import SDKMemoryPolicy


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

    def __init__(self) -> None:
        self._settings_mapper = OpenAIModelSettingsMapper()
        self._tool_bridge = OpenAIToolBridge()
        self._result_mapper = OpenAIAgentsResultMapper()

    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult:
        token = memory_usage.set(Usage())
        model_token = model_usages.set([])
        try:
            return await self._run_with_errors(request, observer)
        except AgentRuntimePolicyError as exc:
            exc.metadata["model_usages"] = TypeAdapter(
                tuple[AgentRuntimeModelUsage, ...]
            ).dump_python(tuple(model_usages.get() or ()), mode="json")
            raise
        finally:
            memory_usage.reset(token)
            model_usages.reset(model_token)

    async def _run_with_errors(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult:
        try:
            return await self._run_once_sdk(request, observer)
        except UserError as exc:
            cause = exc.__cause__
            while cause is not None:
                if isinstance(cause, AgentRuntimePolicyError):
                    raise cause from cause.__cause__
                cause = cause.__cause__
            raise
        except MaxTurnsExceeded as exc:
            raise AgentRuntimePolicyError(
                code="agent_max_turns_exceeded",
                message="Agent run reached its authorized turn limit",
                event_type="agent.run.max_turns_exceeded",
                metadata={"max_turns": request.max_turns},
            ) from exc
        except ModelRefusalError as exc:
            raise AgentRuntimePolicyError(
                code="model_output_refused",
                message="The model refused to produce the requested output",
                event_type="agent.model.refused",
                metadata={"provider": request.provider, "model": request.model},
            ) from exc
        except ModelBehaviorError as exc:
            if request.handoffs or request.agent_tools:
                raise graph_model_error(exc, request) from exc
            raise AgentRuntimeProviderError(
                code="provider_malformed_response",
                message=str(exc),
                retryable=False,
            ) from exc
        except OpenAIError as exc:
            if request.handoffs or request.agent_tools:
                raise graph_model_error(exc, request) from exc
            status_code = getattr(exc, "status_code", None)
            transport_failure = isinstance(exc, APIConnectionError | APITimeoutError)
            code = (
                "provider_rate_limited"
                if status_code == 429
                else "provider_unavailable"
                if (isinstance(status_code, int) and status_code >= 500) or transport_failure
                else "provider_request_failed"
            )
            retryable = (
                status_code == 429
                or (isinstance(status_code, int) and status_code >= 500)
                or transport_failure
            )
            raise AgentRuntimeProviderError(
                code=code,
                message=str(exc),
                retryable=retryable,
            ) from exc

    async def _run_once_sdk(
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

        session = cast(Session | None, request.session)
        result: RunResult | RunResultStreaming
        try:
            if request.stream or request.cancellation is not None:
                result = await run_openai_streamed(
                    request=request,
                    agent=agent,
                    runner_input=runner_input,
                    hooks=hooks,
                    run_config=self._run_config(request),
                    session=session,
                    observer=observer,
                )
            else:
                result = await Runner.run(
                    agent,
                    runner_input,
                    context=None if request.resume_state is not None else request.context,
                    max_turns=request.max_turns,
                    hooks=hooks,
                    run_config=self._run_config(request),
                    session=session,
                )
        except OpenAIRuntimeOutputSchemaError as exc:
            raise exc.policy_error from exc
        except (
            InputGuardrailTripwireTriggered,
            OutputGuardrailTripwireTriggered,
        ) as exc:
            raise _guardrail_blocked_error(exc, guardrail_results) from exc
        usage = result.context_wrapper.usage
        meter = memory_usage.get()
        if isinstance(usage, Usage) and meter is not None:
            usage.add(meter)
        guardrail_results = merged_openai_guardrail_results(result, guardrail_results)
        interruptions = self._result_mapper.interruptions(result)
        final_output, structured_output = self._result_mapper.final_output(result)
        final_agent = result.last_agent
        if not isinstance(final_agent, GovernedAgent | GovernedSandboxAgent):
            raise ValueError("SDK final agent is outside the authorized graph")
        final_request = final_agent.runtime_request
        if final_request is None or final_agent.runtime_ref is None:
            raise ValueError("SDK final agent is outside the authorized graph")
        if final_request.output_schema is not None and not interruptions:
            structured_output = AgentRuntimeStructuredOutput(
                value=result.final_output,
                schema_name=final_request.output_schema.name,
                schema_version=final_request.output_schema.version,
                validated=True,
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
                    "metadata": handoff.metadata,
                },
            )
            for handoff in handoffs
        )
        runtime_events.extend(_agent_tool_events(agent_tool_calls))
        runtime_events.extend(guardrail_events(guardrail_results))
        return AgentRunResult(
            final_output=final_output,
            final_agent=final_agent.runtime_ref,
            raw_output=self._result_mapper.safe_raw_output(result),
            events=tuple(runtime_events),
            resume_state=self._result_mapper.resume_state(result),
            interruptions=tuple(interruptions),
            structured_output=structured_output,
            stream_events=(),
            handoffs=tuple(handoffs),
            agent_tool_calls=tuple(agent_tool_calls),
            guardrail_results=tuple(guardrail_results),
            model_usages=tuple(model_usages.get() or ()),
            usage=runtime_usage(TypeAdapter(Usage).dump_python(usage, mode="json"))
            if isinstance(usage, Usage)
            else None,
        )

    def _validate_request(self, request: AgentRunRequest) -> None:
        self._validate_agent_tools(request)

    async def _runner_input(
        self,
        request: AgentRunRequest,
        agent: Agent[Any],
    ) -> str | list[Any] | RunState[Any]:
        if request.resume_state is None:
            if not request.attachments:
                return request.input_text
            content: list[dict[str, object]] = [{"type": "input_text", "text": request.input_text}]
            for attachment in request.attachments:
                if attachment.kind == "audio":
                    continue
                if (
                    attachment.content_type.startswith("text/")
                    or attachment.content_type == "application/json"
                ):
                    continue
                encoded = base64.b64encode(attachment.content).decode("ascii")
                if attachment.kind == "image":
                    content.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{attachment.content_type};base64,{encoded}",
                            "detail": "auto",
                        }
                    )
                else:
                    content.append(
                        {
                            "type": "input_file",
                            "filename": attachment.filename,
                            "file_data": f"data:{attachment.content_type};base64,{encoded}",
                        }
                    )
            return [{"role": "user", "content": content}]
        if request.resume_state.provider != "openai_agents":
            raise ValueError("OpenAI Agents runner cannot restore another provider's state")
        try:
            state_payload = json.loads(request.resume_state.serialized_state)
        except json.JSONDecodeError as exc:
            raise ValueError("Stored OpenAI Agents run state is not valid JSON") from exc
        if not isinstance(state_payload, dict):
            raise ValueError("Stored OpenAI Agents run state must be a JSON object")

        def restore_context(payload: Mapping[str, Any]) -> AgentRuntimeContext:
            restored = TypeAdapter(AgentRuntimeContext).validate_python(payload)
            if _sdk_authorization_context(restored) != _sdk_authorization_context(request.context):
                raise ValueError("Stored SDK context does not match the frozen run context")
            return request.context

        state = await RunState.from_json(
            initial_agent=agent,
            state_json=state_payload,
            context_override=RunContextWrapper(context=request.context),
            strict_context=True,
            context_deserializer=restore_context,
        )
        if not request.approval_decisions:
            return state
        sdk_interruptions = [
            item
            for item in state.get_interruptions()
            if item.call_id is not None and item.name is not None
        ]
        interruptions = {(item.call_id, item.name): item for item in sdk_interruptions}
        if len(interruptions) != len(sdk_interruptions):
            raise ValueError("Stored OpenAI Agents state contains duplicate interruptions")
        decision_keys = {
            (decision.tool_call_id, decision.tool_name) for decision in request.approval_decisions
        }
        if len(decision_keys) != len(request.approval_decisions):
            raise ValueError("OpenAI approval decisions must be unique")
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
        sdk_name: str | None = None,
    ) -> GovernedAgent | GovernedSandboxAgent:
        profile = request.agent_profile
        capabilities = sandbox_capabilities(request) if self._uses_sdk_sandbox(request) else []
        model_name = request.model or profile.model
        if request.api_key is None:
            raise ValueError("OpenAI-compatible runtime requires an explicit provider API key")
        model: Model = OpenAIProvider(
            api_key=request.api_key,
            base_url=request.base_url,
            use_responses=_use_responses_api(request.model_api),
        ).get_model(model_name)
        if request.handoffs or request.agent_tools or sdk_name is not None:
            model = GraphModel(model, request)
        nested_calls = agent_tool_calls if agent_tool_calls is not None else []
        runtime_guardrail_results = guardrail_results if guardrail_results is not None else []
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
                sdk_name=sdk_name or f"agent_{profile.id}",
            )
        )
        input_guardrails, output_guardrails = _openai_guardrails(
            request,
            runtime_guardrail_results,
        )
        agent_class = GovernedSandboxAgent if self._uses_sdk_sandbox(request) else GovernedAgent
        return agent_class(
            **cast(
                dict[str, Any],
                {"capabilities": capabilities} if self._uses_sdk_sandbox(request) else {},
            ),
            name=sdk_name or f"agent_{profile.id}",
            runtime_request=request,
            runtime_ref=AgentRuntimeAgentRef(
                name=profile.name, profile_id=profile.id, role=profile.role
            ),
            instructions=profile.instructions,
            model=model,
            model_settings=self._settings_mapper.map_settings(profile.model_settings),
            tools=tools,
            mcp_servers=runtime_mcp_servers(request),
            handoffs=self._build_handoffs(
                request,
                handoff_audits if handoff_audits is not None else {},
                guardrail_results=runtime_guardrail_results,
                calls=nested_calls,
                sdk_name=sdk_name or f"agent_{profile.id}",
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
        definitions: tuple[AgentRuntimeAgentTarget, ...],
        *,
        source: AgentRuntimeAgentRef,
        calls: list[AgentRuntimeAgentToolResult],
        guardrail_results: list[AgentRuntimeGuardrailResult],
        sdk_name: str,
    ) -> list[Any]:
        tools: list[Any] = []
        for definition in definitions:
            target = self._build_agent_tool_target(
                request,
                definition,
                calls=calls,
                guardrail_results=guardrail_results,
                sdk_name=f"{sdk_name}/{definition.tool_name}",
            )
            target_request = target.runtime_request
            if target_request is None:
                raise ValueError("Agent tool target has no authorized request")
            authorized_target_request = target_request

            async def extract_output(
                result: Any,
                *,
                item: AgentRuntimeAgentTarget = definition,
                source_ref: AgentRuntimeAgentRef = source,
            ) -> str:
                invocation = getattr(result, "agent_tool_invocation", None)
                status = (
                    "waiting_approval" if getattr(result, "interruptions", None) else "completed"
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
                item: AgentRuntimeAgentTarget = definition,
                source_ref: AgentRuntimeAgentRef = source,
                failed_request: AgentRunRequest = authorized_target_request,
            ) -> str:
                if isinstance(exc, AgentRuntimePolicyError):
                    raise exc
                if isinstance(exc, OpenAIError | ModelBehaviorError):
                    raise graph_model_error(exc, failed_request) from exc
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
                    hooks=OpenAIRuntimeHooks.for_request(request),
                )
            )
        return tools

    def _build_agent_tool_target(
        self,
        request: AgentRunRequest,
        definition: AgentRuntimeAgentTarget,
        *,
        calls: list[AgentRuntimeAgentToolResult],
        guardrail_results: list[AgentRuntimeGuardrailResult],
        sdk_name: str,
        handoff_audits: dict[str, dict[str, object]] | None = None,
    ) -> GovernedAgent | GovernedSandboxAgent:
        if not is_openai_compatible_provider(definition.provider):
            raise ValueError("OpenAI agent targets require an OpenAI-compatible provider")
        profile = AgentRuntimeProfile(
            id=definition.target.ref.profile_id,
            workspace_id=definition.target.workspace_id,
            version=definition.target.version,
            name=definition.target.ref.name,
            role=definition.target.ref.role or "expert",
            instructions=definition.target.instructions,
            model=definition.model,
            model_settings=definition.target.model_settings,
        )
        scoped_request = replace(
            request,
            agent_profile=profile,
            context=definition.context,
            model=definition.model,
            provider=definition.provider,
            base_url=definition.base_url,
            api_key=definition.api_key,
            model_api=definition.model_api,
            model_provider_credential_id=definition.model_provider_credential_id,
            agent_tools=definition.nested_tools,
            handoffs=definition.nested_handoffs,
            output_schema=definition.output_schema,
            guardrails=definition.guardrails,
            require_handoff=definition.require_handoff,
            max_turns=definition.max_turns,
        )
        return self._build_agent(
            scoped_request,
            handoff_audits=handoff_audits,
            agent_tool_calls=calls,
            guardrail_results=guardrail_results,
            sdk_name=sdk_name,
        )

    def _validate_agent_tools(self, request: AgentRunRequest) -> None:
        if request.require_handoff and not request.handoffs:
            raise ValueError("Routing-only agent requires handoff targets")
        self._validate_agent_tool_level(
            (*request.agent_tools, *request.handoffs),
            workspace_id=request.context.workspace_id,
            task_id=request.context.task_id,
            run_id=request.context.run_id,
            expected_depth=1,
            product_tool_names=set(request.context.allowed_tools),
            path=(getattr(request.agent_profile, "id", None),),
        )

    def _validate_agent_tool_level(
        self,
        definitions: tuple[AgentRuntimeAgentTarget, ...],
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
            if (
                definition.nested_tools or definition.nested_handoffs
            ) and definition.depth >= definition.max_depth:
                raise ValueError("Agent tool graph exceeds its maximum depth")
            self._validate_agent_tool_level(
                (*definition.nested_tools, *definition.nested_handoffs),
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
        calls: list[AgentRuntimeAgentToolResult],
        sdk_name: str,
    ) -> list[Any]:
        handoffs: list[Any] = []
        for definition in request.handoffs:
            target = self._build_agent_tool_target(
                request,
                definition,
                calls=calls,
                guardrail_results=guardrail_results,
                handoff_audits=handoff_audits,
                sdk_name=f"{sdk_name}/{definition.tool_name}",
            )
            handoff_audits[target.name] = {
                "source_agent_profile_id": str(request.agent_profile.id),
                "target_agent_profile_id": str(definition.target.ref.profile_id),
            }
            handoffs.append(
                sdk_handoff(
                    target,
                    tool_name_override=definition.tool_name,
                    tool_description_override=definition.description,
                )
            )
        return handoffs

    @staticmethod
    def _uses_sdk_sandbox(request: AgentRunRequest) -> bool:
        # Native sandbox tools require Responses. Chat Completions still runs in
        # the same approved Runtime, with its supported product/MCP tools.
        supported = _use_responses_api(request.model_api) is not False
        if (
            not supported
            and SDKMemoryPolicy.model_validate(
                request.context.metadata.get("sdk_memory", {})
            ).enabled
        ):
            raise AgentRuntimePolicyError(
                code="sdk_memory_api_unsupported",
                message="Native SDK Memory requires the Responses API",
                event_type="runtime.authorization_blocked",
                metadata={},
            )
        return request.sandbox is not None and supported

    def _run_config(self, request: AgentRunRequest) -> RunConfig | None:
        tracing = request.tracing
        return RunConfig(
            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
            workflow_name=tracing.workflow_name if tracing else "OpsMesh agent run",
            trace_id=tracing.trace_id if tracing else None,
            group_id=tracing.group_id if tracing else None,
            trace_metadata=tracing.metadata if tracing else None,
            tracing_disabled=tracing.disabled if tracing else True,
            trace_include_sensitive_data=tracing.include_sensitive_data if tracing else False,
            sandbox=(
                sandbox_run_config(request.sandbox)
                if request.sandbox is not None
                and (
                    self._uses_sdk_sandbox(request)
                    or _targets_use_sandbox((*request.handoffs, *request.agent_tools))
                )
                else None
            ),
        )


def _sdk_authorization_context(context: AgentRuntimeContext) -> AgentRuntimeContext:
    binding = context.runtime_binding
    return replace(
        context,
        runtime_binding=replace(binding, execution_runtime_id=None) if binding else None,
        metadata={
            key: context.metadata.get(key)
            for key in (
                "authorization_snapshot_fingerprint",
                "authorization_snapshot_version",
                "capability_catalog_fingerprint",
                "agent_profile_id",
                "agent_profile_version",
                "run_model",
                "model_provider_provider",
                "model_provider_credential_id",
                "model_provider_model_api",
                "sdk_memory",
                "runtime_policy",
                "approval_policy",
                "persistent_session_key",
                "persistent_session_mode",
            )
        },
    )


def _targets_use_sandbox(targets: tuple[AgentRuntimeAgentTarget, ...]) -> bool:
    return any(
        _use_responses_api(target.model_api) is not False
        or _targets_use_sandbox((*target.nested_tools, *target.nested_handoffs))
        for target in targets
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
    item: AgentRuntimeAgentTarget,
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


def _use_responses_api(model_api: str | None) -> bool | None:
    normalized = canonical_model_api(model_api)
    if normalized == OPENAI_CHAT_COMPLETIONS_API:
        return False
    if normalized == OPENAI_RESPONSES_API:
        return True
    return None
