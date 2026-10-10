"""Explicit JSON contract; live adapters and database credentials never cross this boundary."""

import base64
from dataclasses import fields
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from opsmesh.agents.execution.contracts import (
    AgentInputAttachment,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentTool,
    AgentRuntimeApprovalDecision,
    AgentRuntimeContext,
    AgentRuntimeGuardrails,
    AgentRuntimeHandoff,
    AgentRuntimeOutputSchema,
    AgentRuntimeProfile,
    AgentRuntimeResumeState,
    AgentRuntimeStreamEvent,
    AgentRuntimeToolResult,
    AgentRunTracing,
)
from opsmesh.runtime.contracts import SandboxManifest

MAX_FRAME_BYTES = 16_777_216
RESULT = TypeAdapter(AgentRunResult)
CONTEXT = TypeAdapter(AgentRuntimeContext)
TOOL_RESULT = TypeAdapter(AgentRuntimeToolResult)
STREAM_EVENT = TypeAdapter(AgentRuntimeStreamEvent)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class WireAttachment(WireModel):
    file_id: str
    kind: str
    filename: str
    content_type: str
    content: str = Field(repr=False)
    transcript: str = Field(repr=False)

    def attachment(self) -> AgentInputAttachment:
        from uuid import UUID

        return AgentInputAttachment(
            UUID(self.file_id),
            self.kind,
            self.filename,
            self.content_type,
            base64.b64decode(self.content, validate=True),
            self.transcript,
        )


class RuntimeRunInput(WireModel):
    agent_profile: AgentRuntimeProfile
    input_text: str = Field(repr=False)
    context: AgentRuntimeContext
    max_turns: int
    model: str | None
    provider: str | None
    base_url: str | None = Field(repr=False)
    api_key: str | None = Field(repr=False)
    model_api: str | None
    model_provider_credential_id: str | None
    tracing: AgentRunTracing | None
    resume_state: AgentRuntimeResumeState | None = Field(repr=False)
    approval_decisions: tuple[AgentRuntimeApprovalDecision, ...]
    handoffs: tuple[AgentRuntimeHandoff, ...]
    handoff_agents: tuple[AgentRuntimeAgentDefinition, ...]
    agent_tools: tuple[AgentRuntimeAgentTool, ...] = Field(repr=False)
    output_schema: AgentRuntimeOutputSchema | None
    guardrails: AgentRuntimeGuardrails | None
    stream: bool
    session_id: str | None
    tools_enabled: bool
    manifest: SandboxManifest
    persistent: bool
    sandbox_timeout_seconds: int = Field(gt=0)
    sandbox_max_file_bytes: int = Field(gt=0)
    attachments: tuple[WireAttachment, ...] = Field(repr=False)

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> "RuntimeRunInput":
        if request.sandbox is None:
            raise ValueError("Agent SDK execution requires an authorized Runtime")
        from opsmesh.runtime.backends.docker import DockerSandboxSessionExecutor

        sandbox_executor = request.sandbox.session.executor
        if not isinstance(sandbox_executor, DockerSandboxSessionExecutor):
            raise ValueError("Agent SDK execution requires a Docker Runtime")
        excluded = {
            "session",
            "tool_executor",
            "cancellation",
            "sandbox",
            "event_sink",
            "attachments",
        }
        payload = {
            field.name: getattr(request, field.name)
            for field in fields(request)
            if field.name not in excluded
        }
        credential_id = request.model_provider_credential_id
        payload["model_provider_credential_id"] = str(credential_id) if credential_id else None
        return cls.model_validate(
            {
                **payload,
                "session_id": request.session.session_id if request.session else None,
                "tools_enabled": request.tool_executor is not None,
                "manifest": request.sandbox.manifest,
                "persistent": request.sandbox.session.persistent,
                "sandbox_timeout_seconds": sandbox_executor.timeout_seconds,
                "sandbox_max_file_bytes": sandbox_executor.max_file_bytes,
                "attachments": [
                    WireAttachment(
                        file_id=str(a.file_id),
                        kind=a.kind,
                        filename=a.filename,
                        content_type=a.content_type,
                        content=base64.b64encode(a.content).decode("ascii"),
                        transcript=a.transcript,
                    )
                    for a in request.attachments
                ],
            }
        )


class RpcFrame(WireModel):
    type: Literal["start", "ready", "call", "reply", "event", "result", "error", "log"]
    id: str | None = None
    method: str | None = None
    payload: object = Field(default=None, repr=False)
    error: dict[str, object] | None = None

    def encoded(self) -> bytes:
        encoded = self.model_dump_json().encode("utf-8") + b"\n"
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError("Runtime RPC frame exceeds its transfer limit")
        return encoded
