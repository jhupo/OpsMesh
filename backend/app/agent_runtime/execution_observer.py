from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.agent_runtime.core.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeStreamEvent,
    AgentRuntimeStreamEventKind,
)
from backend.app.security.redaction import redact_sensitive_payload


@dataclass(slots=True)
class AgentRuntimeExecutionObserver:
    request: AgentRunRequest
    events: list[AgentRuntimeEvent] = field(default_factory=list)
    stream_events: list[AgentRuntimeStreamEvent] = field(default_factory=list)

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
        self.stream_events.append(
            AgentRuntimeStreamEvent(
                sequence=len(self.stream_events) + 1,
                event_type=event_type,
                payload=redact_sensitive_payload(payload or {}),
                delta=redacted_delta,
                is_terminal=terminal,
            )
        )

    def finish(self, result: AgentRunResult) -> None:
        if not self.request.stream:
            return
        event_type = (
            AgentRuntimeStreamEventKind.RUN_PAUSED.value
            if result.resume_state is not None or result.interruptions
            else AgentRuntimeStreamEventKind.RUN_COMPLETED.value
        )
        self.stream(event_type, terminal=True)
