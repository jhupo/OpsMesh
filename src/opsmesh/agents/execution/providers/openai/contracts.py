"""Serializable SDK execution definitions; credentials remain Activity-owned."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentGuardrailConfig,
    AgentInputAttachment,
    AgentInvocationConfig,
    AgentOutputSchemaSpec,
    AgentProfileSnapshot,
    AgentResumeState,
    AgentRunRequest,
    AgentTargetProfile,
    AgentToolApprovalDecision,
    AgentTracingConfig,
)


class AgentModelReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    run_id: UUID
    agent_profile_id: UUID
    credential_id: UUID
    model: str
    model_api: str | None = None
    purpose: Literal["agent", "memory"] = "agent"

    def encode(self) -> str:
        return "opsmesh:" + self.model_dump_json()

    @classmethod
    def decode(cls, name: str | None) -> AgentModelReference:
        if name is None or not name.startswith("opsmesh:"):
            raise ValueError("Model activity requires a scoped OpsMesh model reference")
        return cls.model_validate_json(name[len("opsmesh:") :])


class AgentSessionReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    run_id: UUID
    session_id: str


class AgentInvocationDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: AgentTargetProfile
    tool_name: str
    description: str
    context: AgentExecutionContext
    max_turns: int
    depth: int
    max_depth: int
    model_reference: AgentModelReference
    provider: str
    nested_tools: tuple[AgentInvocationDefinition, ...] = ()
    nested_handoffs: tuple[AgentInvocationDefinition, ...] = ()
    output_schema: AgentOutputSchemaSpec | None = None
    guardrails: AgentGuardrailConfig | None = None
    require_handoff: bool = False

    @classmethod
    def from_target(cls, target: AgentInvocationConfig) -> AgentInvocationDefinition:
        profile_id = target.target.ref.profile_id
        credential_id = target.model_provider_credential_id
        if profile_id is None or credential_id is None or target.provider is None:
            raise ValueError("Agent target requires explicit authorized model references")
        return cls(
            target=target.target,
            tool_name=target.tool_name,
            description=target.description,
            context=target.context,
            max_turns=target.max_turns,
            depth=target.depth,
            max_depth=target.max_depth,
            model_reference=AgentModelReference(
                workspace_id=target.context.workspace_id,
                run_id=target.context.run_id,
                agent_profile_id=profile_id,
                credential_id=credential_id,
                model=target.model,
                model_api=target.model_api,
            ),
            provider=target.provider,
            nested_tools=tuple(cls.from_target(item) for item in target.nested_tools),
            nested_handoffs=tuple(cls.from_target(item) for item in target.nested_handoffs),
            output_schema=target.output_schema,
            guardrails=target.guardrails,
            require_handoff=target.require_handoff,
        )

    def to_target(self) -> AgentInvocationConfig:
        return AgentInvocationConfig(
            target=self.target,
            tool_name=self.tool_name,
            description=self.description,
            context=self.context,
            max_turns=self.max_turns,
            depth=self.depth,
            max_depth=self.max_depth,
            model=self.model_reference.model,
            provider=self.provider,
            model_api=self.model_reference.model_api,
            model_provider_credential_id=self.model_reference.credential_id,
            nested_tools=tuple(item.to_target() for item in self.nested_tools),
            nested_handoffs=tuple(item.to_target() for item in self.nested_handoffs),
            output_schema=self.output_schema,
            guardrails=self.guardrails,
            require_handoff=self.require_handoff,
        )


class AgentExecutionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: AgentProfileSnapshot
    context: AgentExecutionContext
    model_reference: AgentModelReference
    provider: str
    input_text: str
    max_turns: int
    session: AgentSessionReference | None = None
    tracing: AgentTracingConfig | None = None
    resume_state: AgentResumeState | None = None
    approval_decisions: tuple[AgentToolApprovalDecision, ...] = ()
    handoffs: tuple[AgentInvocationDefinition, ...] = ()
    agent_tools: tuple[AgentInvocationDefinition, ...] = ()
    require_handoff: bool = False
    output_schema: AgentOutputSchemaSpec | None = None
    guardrails: AgentGuardrailConfig | None = None
    attachments: tuple[AgentInputAttachment, ...] = ()
    stream: bool = False
    sandbox_backend: str | None = None

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> AgentExecutionDefinition:
        profile_id = request.agent_profile.id
        credential_id = request.model_provider_credential_id
        if profile_id is None or credential_id is None or request.provider is None:
            raise ValueError("Agent execution requires explicit authorized model references")
        session = request.session
        return cls(
            profile=request.agent_profile,
            context=request.context,
            model_reference=AgentModelReference(
                workspace_id=request.context.workspace_id,
                run_id=request.context.run_id,
                agent_profile_id=profile_id,
                credential_id=credential_id,
                model=request.model or request.agent_profile.model,
                model_api=request.model_api,
            ),
            provider=request.provider,
            input_text=request.input_text,
            max_turns=request.max_turns,
            session=AgentSessionReference(
                workspace_id=request.context.workspace_id,
                run_id=request.context.run_id,
                session_id=session.session_id,
            )
            if session is not None
            else None,
            tracing=request.tracing,
            resume_state=request.resume_state,
            approval_decisions=request.approval_decisions,
            handoffs=tuple(
                AgentInvocationDefinition.from_target(item) for item in request.handoffs
            ),
            agent_tools=tuple(
                AgentInvocationDefinition.from_target(item) for item in request.agent_tools
            ),
            require_handoff=request.require_handoff,
            output_schema=request.output_schema,
            guardrails=request.guardrails,
            attachments=request.attachments,
            stream=request.stream,
            sandbox_backend=(
                str(request.context.metadata["sandbox_backend_id"])
                if isinstance(request.context.metadata.get("sandbox_backend_id"), str)
                else None
            ),
        )
