"""Reject unsupported neutral features before accepting a run."""

from opsmesh.framework.agents import AgentSpecification, OpsMeshAgentBackend
from opsmesh.framework.errors import OpsMeshError


def validate_backend(spec: AgentSpecification, backend: OpsMeshAgentBackend) -> None:
    requirements = {
        "structured_output": spec.output_schema is not None or spec.output_type_ref is not None,
        "handoffs": bool(spec.handoffs),
        "agent_tools": bool(spec.agent_tools),
        "sandbox": spec.sandbox is not None,
        "pre_approval_guardrails": spec.pre_approval_tool_input_guardrails,
        "function_tool_concurrency": spec.max_function_tool_concurrency is not None,
        "session_compaction": spec.session_compaction is not None,
        "custom_tool_completion": spec.tool_completion_ref is not None,
        "structured_agent_input": any(
            t.input_type_ref or t.input_builder_ref for t in spec.agent_tools
        ),
        "approvals": any(tool.requires_approval for tool in spec.tools)
        or any(tool.requires_approval for tool in spec.agent_tools)
        or any(getattr(tool, "requires_approval", False) for tool in spec.native_tools)
        or any(
            tool.type == "mcp" and tool.require_approval == "always" for tool in spec.hosted_tools
        ),
    }
    unsupported = [
        name
        for name, required in requirements.items()
        if required and not getattr(backend.features, name)
    ]
    stages = {guard.stage for guard in spec.guardrails} - set(backend.features.guardrail_stages)
    unsupported.extend(
        sorted({tool.type for tool in spec.hosted_tools} - set(backend.features.hosted_tools))
    )
    unsupported.extend(
        sorted({tool.type for tool in spec.native_tools} - set(backend.features.native_tools))
    )
    if spec.model_transport not in backend.features.model_transports:
        unsupported.append(spec.model_transport)
    if unsupported or stages:
        raise OpsMeshError(
            "FEATURE_UNSUPPORTED",
            "Backend cannot implement the requested features",
            details={"features": unsupported, "guardrail_stages": sorted(stages)},
        )
