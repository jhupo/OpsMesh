from backend.app.agent_runtime.core.contracts import (
    AgentRunRequest,
    AgentRuntimeCapability,
)


def required_runtime_capabilities(
    request: AgentRunRequest,
) -> tuple[AgentRuntimeCapability, ...]:
    required = {
        AgentRuntimeCapability.LIFECYCLE_EVENTS,
        AgentRuntimeCapability.USAGE,
    }
    if request.context.tool_definitions:
        required.add(AgentRuntimeCapability.TOOLS)
    if request.handoffs or request.handoff_agents:
        required.add(AgentRuntimeCapability.HANDOFFS)
    if request.agent_tools:
        required.add(AgentRuntimeCapability.AGENTS_AS_TOOLS)
    if request.output_schema is not None:
        required.add(AgentRuntimeCapability.STRUCTURED_OUTPUT)
    if request.stream:
        required.add(AgentRuntimeCapability.STREAMING)
    if request.resume_state is not None or request.approval_decisions:
        required.add(AgentRuntimeCapability.RESUMABLE_STATE)
    if request.guardrails is not None:
        required.add(AgentRuntimeCapability.GUARDRAILS)
    if request.session is not None:
        required.add(AgentRuntimeCapability.SESSIONS)
    if request.cancellation is not None:
        required.add(AgentRuntimeCapability.CANCELLATION)
    return tuple(sorted(required, key=lambda item: item.value))
