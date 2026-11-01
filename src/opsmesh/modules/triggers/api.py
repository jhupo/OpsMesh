"""Versioned event trigger definitions and durable delivery contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol, Self

from pydantic import Field, JsonValue, model_validator

from opsmesh.framework.adapters import ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import ErrorInfo
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.runtime.api import AgentRunView, RunOverrides


class TriggerDefinition(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    revision: int = Field(default=1, ge=1)
    principal_id: str = Field(min_length=1)
    agent_revision: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=200)
    filters: dict[str, JsonValue] = Field(default_factory=dict, max_length=30)
    input: str | list[dict[str, Any]] | None = None
    input_mapping: dict[str, str] = Field(default_factory=dict, max_length=30)
    run_overrides: RunOverrides = Field(default_factory=RunOverrides)
    session_policy: Literal["per_event", "shared"] = "per_event"
    session_id: str | None = Field(default=None, min_length=1, max_length=150)
    reply_target: ReplyTarget | None = None
    notify_on_failure: bool = True
    notify_on_approval: bool = True
    webhook_secret_ref: str | None = Field(default=None, pattern=r"^env:[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True

    @model_validator(mode="after")
    def valid_mapping(self) -> Self:
        if self.input is not None and self.input_mapping:
            raise ValueError("Choose fixed input or an event input mapping")
        if self.session_id and self.session_policy != "shared":
            raise ValueError("An explicit session_id requires shared session policy")
        for path in (*self.filters, *self.input_mapping.values()):
            if path not in {"id", "type", "source", "occurred_at"} and not path.startswith(
                "payload."
            ):
                raise ValueError("Event paths must select an envelope field or payload field")
            if len(path) > 200 or any(not segment for segment in path.split(".")):
                raise ValueError("Event field paths must be bounded nonempty dotted names")
        return self


class TriggerInvocation(OpsMeshContract):
    trigger_id: str
    trigger_revision: int
    delivery_id: str
    event_id: str
    principal_id: str
    agent_revision: str
    input: str | list[dict[str, Any]]
    run_overrides: RunOverrides
    session_policy: Literal["per_event", "shared"]
    session_id: str | None
    reply_target: ReplyTarget | None
    notify_on_failure: bool
    notify_on_approval: bool
    idempotency_key: str


class TriggerDelivery(OpsMeshContract):
    id: str
    trigger_id: str
    trigger_revision: int
    event_id: str
    status: Literal[
        "dispatching",
        "submitted",
        "running",
        "waiting_approval",
        "completed",
        "cancelled",
        "blocked",
        "failed",
        "ignored",
    ]
    run_id: str | None = None
    reason: str | None = None
    error: ErrorInfo | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


TriggerAuthorizer = Callable[[str, str, str], Awaitable[None]]
TriggerAgentResolver = Callable[[str], Awaitable[AgentSpecification]]
TriggerRunResolver = Callable[[str], Awaitable[AgentRunView]]
TriggerSubmissionResolver = Callable[[str, str], Awaitable[AgentRunView | None]]
TriggerSubmitter = Callable[[TriggerInvocation], Awaitable[str]]
TriggerTargetValidator = Callable[[TriggerDefinition, str], Awaitable[None]]
TriggerSecretResolver = Callable[[str], str]


class TriggerService(Protocol):
    async def create(
        self, definition: TriggerDefinition, *, actor_id: str
    ) -> TriggerDefinition: ...
    async def get(self, trigger_id: str, *, actor_id: str) -> TriggerDefinition: ...
    async def list_triggers(self, *, actor_id: str) -> list[TriggerDefinition]: ...
    async def update(
        self,
        trigger_id: str,
        definition: TriggerDefinition,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> TriggerDefinition: ...
    async def delete(self, trigger_id: str, expected_revision: int, *, actor_id: str) -> None: ...
    async def set_enabled(
        self,
        trigger_id: str,
        enabled: bool,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> TriggerDefinition: ...
    async def dispatch_event(
        self, event: OpsMeshEvent, *, actor_id: str
    ) -> list[TriggerDelivery]: ...
    async def ingest_webhook(
        self,
        trigger_id: str,
        body: bytes,
        *,
        timestamp: str,
        delivery_id: str,
        signature: str,
    ) -> TriggerDelivery: ...
    async def list_deliveries(
        self,
        trigger_id: str,
        *,
        actor_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TriggerDelivery]: ...
