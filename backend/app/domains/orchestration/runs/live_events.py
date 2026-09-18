"""Immediate, provider-neutral previews; durable Task/Run results remain authoritative."""

from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID, uuid4

from backend.app.core.security.redaction import redact_sensitive_text
from backend.app.domains.agents.runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeStreamEvent,
    AgentRuntimeToolExecutor,
    AgentRuntimeToolResult,
)
from backend.app.domains.orchestration.tasks.events import TaskEventBus


@dataclass
class RunLivePublisher:
    bus: TaskEventBus
    workspace_id: UUID
    task_id: UUID
    run_id: UUID
    step_id: UUID | None
    node_id: str | None
    allow_text: bool = True
    attempt_id: UUID = field(default_factory=uuid4)
    sequence: int = 0
    text: str = ""
    last_text_at: float = 0
    text_dirty: bool = False
    text_truncated: bool = False

    def publish(self, kind: str, data: dict[str, object]) -> None:
        self.sequence += 1
        self.bus.publish(
            workspace_id=self.workspace_id,
            task_id=self.task_id,
            event_type="run.live",
            event_id=f"{self.attempt_id}:{self.sequence}",
            payload={
                "kind": kind,
                "run_id": str(self.run_id),
                "step_id": str(self.step_id) if self.step_id else None,
                "node_id": self.node_id,
                "attempt_id": str(self.attempt_id),
                "sequence": self.sequence,
                "data": data,
            },
        )

    def __call__(self, event: AgentRuntimeStreamEvent) -> None:
        if event.event_type == "run.started":
            self.publish("output.reset", {})
        elif event.event_type == "output.text.delta" and event.delta and self.allow_text:
            # Re-redact the accumulated preview so matches spanning provider chunks are covered.
            # A replacement event lets clients correct an earlier incomplete preview.
            combined = self.text + event.delta
            self.text_truncated = self.text_truncated or len(combined) > 32_000
            self.text = combined[:32_000]
            self.text_dirty = True
            if monotonic() - self.last_text_at >= 0.1:
                self.flush_text()
        elif event.is_terminal:
            self.flush_text()
        elif event.event_type in {
            "agent.tool.started",
            "agent.tool.completed",
            "agent.tool.failed",
        }:
            self.publish(
                event.event_type.removeprefix("agent."),
                {
                    "tool_name": redact_sensitive_text(str(event.payload.get("tool_name", ""))),
                    "call_id": event.payload.get("tool_call_id"),
                },
            )

    def flush_text(self) -> None:
        if self.text_dirty:
            self.publish(
                "output.text",
                {
                    "text": redact_sensitive_text(self.text),
                    "provisional": True,
                    "truncated": self.text_truncated,
                },
            )
            self.last_text_at = monotonic()
            self.text_dirty = False


@dataclass
class LiveToolExecutor:
    executor: AgentRuntimeToolExecutor
    publisher: RunLivePublisher

    def review_tool_call(
        self, *, context: AgentRuntimeContext, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        return self.executor.review_tool_call(
            context=context, tool_name=tool_name, arguments=arguments
        )

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> AgentRuntimeToolResult:
        call_id = tool_call_id or str(uuid4())
        data: dict[str, object] = {
            "tool_name": redact_sensitive_text(tool_name),
            "call_id": call_id,
        }
        self.publisher.publish("tool.started", data)
        try:
            result = await self.executor.execute_tool(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
                tool_call_id=call_id,
                approval_granted=approval_granted,
            )
        except BaseException:
            self.publisher.publish("tool.failed", data)
            raise
        kind = (
            "tool.completed"
            if result.status == "completed"
            else "approval.required"
            if result.status == "waiting_approval"
            else "tool.waiting"
            if result.status in {"waiting_runtime", "waiting"}
            else "tool.failed"
        )
        self.publisher.publish(kind, {**data, "status": result.status})
        return result
