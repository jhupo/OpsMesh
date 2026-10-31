"""Construct per-run settings without changing SDK process-global defaults."""

from typing import Any

from agents import RunConfig
from agents.run_config import ToolExecutionConfig

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification

from .sandbox import build_sandbox


def build_run_config(
    spec: AgentSpecification,
    context: AgentExecutionContext,
    *,
    model_provider: Any = None,
    tracing_enabled: bool = False,
) -> RunConfig:
    carrier = context.services.get("trace_context", {})
    trace_metadata = {"run_id": context.run_id}
    trace_metadata.update(
        {
            key: value
            for key, value in carrier.items()
            if key in {"traceparent", "tracestate"} and isinstance(value, str)
        }
    )
    options: dict[str, Any] = {
        "tracing_disabled": not tracing_enabled,
        "trace_include_sensitive_data": False,
        "workflow_name": "opsmesh",
        "group_id": context.session_id,
        "trace_metadata": trace_metadata,
        "tool_execution": ToolExecutionConfig(
            pre_approval_tool_input_guardrails=spec.pre_approval_tool_input_guardrails,
            max_function_tool_concurrency=spec.max_function_tool_concurrency,
        ),
    }
    if model_provider is not None:
        options["model_provider"] = model_provider
    if spec.sandbox:
        options["sandbox"] = build_sandbox(spec.sandbox, context)
    return RunConfig(**options)
