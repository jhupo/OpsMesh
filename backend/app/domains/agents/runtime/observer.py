from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.agents.runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeStreamEvent,
    AgentRuntimeStreamEventKind,
)


@dataclass(slots=True)
class AgentRuntimeExecutionObserver:
    request: AgentRunRequest
    events: list[AgentRuntimeEvent] = field(default_factory=list)
    stream_events: list[AgentRuntimeStreamEvent] = field(default_factory=list)
    stream_sequence: int = 0

    def start(self) -> None:
        if self.request.stream:
            self.stream(AgentRuntimeStreamEventKind.RUN_STARTED.value)

    def lifecycle(
        self,
        event_type: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.events.append(
            AgentRuntimeEvent(
                event_type=event_type,
                message=message,
                payload=redact_sensitive_payload(payload or {}),
            )
        )
        if event_type in {"agent.tool.started", "agent.tool.completed", "agent.tool.failed"}:
            tool_name = (payload or {}).get("tool_name")
            if tool_name not in self.request.context.allowed_tools:
                self.stream(event_type, payload=payload)

    def stream(
        self,
        event_type: str,
        *,
        payload: dict[str, object] | None = None,
        delta: str | None = None,
        terminal: bool = False,
    ) -> None:
        if not self.request.stream:
            return
        redacted_delta = None
        if delta is not None:
            value = redact_sensitive_payload({"value": delta}).get("value")
            redacted_delta = value if isinstance(value, str) else str(value)
        self.stream_sequence += 1
        event = AgentRuntimeStreamEvent(
            sequence=self.stream_sequence,
            event_type=event_type,
            payload=redact_sensitive_payload(payload or {}),
            delta=redacted_delta,
            is_terminal=terminal,
        )
        if self.request.event_sink is not None:
            self.request.event_sink(event)
        else:
            self.stream_events.append(event)

    def finish(self, result: AgentRunResult) -> None:
        if not self.request.stream:
            return
        event_type = (
            AgentRuntimeStreamEventKind.RUN_PAUSED.value
            if result.resume_state is not None or result.interruptions
            else AgentRuntimeStreamEventKind.RUN_COMPLETED.value
        )
        self.stream(event_type, terminal=True)
