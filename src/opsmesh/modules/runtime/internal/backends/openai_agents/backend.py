"""OpenAI Agents SDK adapter orchestration; mapping lives in focused sibling modules."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from importlib.metadata import version
from typing import Any

from agents import Runner
from agents.models.interface import Model, ModelProvider

from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentExecutionContext,
    AgentSpecification,
    BackendOutcome,
    EventSink,
    bind_execution_context,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions

from .agent_builder import AgentGraphBuilder
from .compaction import build_session
from .config import build_run_config
from .errors import translate_error
from .hooks import RegisteredRunHooks, resolve_hooks
from .state import capture_outcome, restore_input
from .streaming import execute_stream
from .usage import publish_unknown_usage

logger = logging.getLogger(__name__)


class OpenAIAgentsBackend:
    id = "openai_agents"
    features = AgentBackendCapabilities(
        streaming=True,
        approvals=True,
        resume=True,
        structured_output=True,
        handoffs=True,
        agent_tools=True,
        sandbox=True,
        hosted_tools=(
            "web_search",
            "file_search",
            "code_interpreter",
            "image_generation",
            "mcp",
            "tool_search",
        ),
        native_tools=("shell", "apply_patch", "computer"),
        model_transports=("http", "websocket"),
        session_compaction=True,
        custom_tool_completion=True,
        structured_agent_input=True,
        pre_approval_guardrails=True,
        function_tool_concurrency=True,
        guardrail_stages=("input", "output", "tool_input", "tool_output"),
    )

    def __init__(
        self,
        extensions: RuntimeExtensions,
        *,
        model_resolver: Callable[[AgentSpecification, AgentExecutionContext], Model | str | None]
        | None = None,
        model_provider: ModelProvider | None = None,
        tracing_enabled: bool = False,
    ) -> None:
        self.extensions = extensions
        self.model_resolver = model_resolver
        self.model_provider = model_provider
        self.tracing_enabled = tracing_enabled
        self.version = version("openai-agents")

    def _builder(self, context: AgentExecutionContext) -> AgentGraphBuilder:
        resolver = self.model_resolver
        if resolver is None and self.model_provider is not None:
            provider = self.model_provider

            def resolve_provider(spec: AgentSpecification, context: AgentExecutionContext) -> Model:
                return provider.get_model(spec.model)

            resolver = resolve_provider
        return AgentGraphBuilder(
            self.extensions, context, lambda spec: self._config(spec, context), resolver
        )

    def _config(self, spec: AgentSpecification, context: AgentExecutionContext) -> Any:
        return build_run_config(
            spec, context, model_provider=self.model_provider, tracing_enabled=self.tracing_enabled
        )

    async def run(
        self,
        spec: AgentSpecification,
        input: str | list[dict[str, Any]],
        context: AgentExecutionContext,
        *,
        checkpoint: dict[str, Any] | None = None,
        decisions: dict[str, bool] | None = None,
        emit: EventSink | None = None,
    ) -> BackendOutcome:
        builder = self._builder(context)
        context.services["backend_event_sink"] = emit
        try:
            with bind_execution_context(context):
                agent = await builder.build(spec)
                run_input = await restore_input(
                    agent, input, context, checkpoint, decisions, self.version, builder.graph
                )
                options = {
                    "context": context,
                    "max_turns": spec.max_turns,
                    "run_config": self._config(spec, context),
                    "hooks": RegisteredRunHooks(resolve_hooks(spec, self.extensions, "run")),
                    "session": build_session(
                        spec, context, agent.model, builder.model_clients.get(spec.ref)
                    ),
                }
                if emit is None:
                    result = await Runner.run(agent, run_input, **options)
                else:
                    result = await execute_stream(agent, run_input, context, options, emit)
                return capture_outcome(result, context, self.version, builder.graph)
        except asyncio.CancelledError:
            await self._unknown_usage(context)
            raise
        except OpsMeshError:
            await self._unknown_usage(context)
            raise
        except Exception as error:
            await self._unknown_usage(context)
            raise translate_error(error) from error
        finally:
            await builder.close()

    async def _unknown_usage(self, context: AgentExecutionContext) -> None:
        try:
            await publish_unknown_usage(context)
        except Exception:
            logger.exception("Unknown model usage needs reconciliation for run %s", context.run_id)
