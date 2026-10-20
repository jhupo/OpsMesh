"""Fixed target and actor; credentials are never exposed in trigger views."""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from opsmesh.orchestration.definitions.contracts import FlowValue


class TriggerConfiguration(FlowValue):
    source_kind: Literal["extension", "run", "platform"] = "extension"
    event_type: str = Field(default="", max_length=200)
    instance_id: UUID | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=31536000)
    cron: str | None = Field(default=None, min_length=1, max_length=200)
    time_zone: str = Field(default="UTC", max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    payload_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    replay_seconds: int = Field(default=300, ge=10, le=3600)


class TriggerCreate(FlowValue):
    revision_id: UUID
    configuration_id: UUID | None = None
    kind: Literal["event", "calendar", "webhook"]
    configuration: TriggerConfiguration
    webhook_secret: str | None = Field(default=None, min_length=32, max_length=200)

    @model_validator(mode="after")
    def complete(self) -> "TriggerCreate":
        if self.kind == "webhook" and self.webhook_secret is None:
            raise ValueError("A webhook requires a signing secret")
        if self.kind != "webhook" and self.webhook_secret is not None:
            raise ValueError("Only webhooks accept signing secrets")
        if self.kind == "event" and not self.configuration.event_type:
            raise ValueError("An event trigger requires an exact namespaced event type")
        if (
            self.kind == "event"
            and self.configuration.source_kind == "extension"
            and self.configuration.instance_id is None
        ):
            raise ValueError("An extension event trigger requires a fixed source instance")
        if self.kind == "calendar" and (self.configuration.interval_seconds is None) == (
            self.configuration.cron is None
        ):
            raise ValueError("A calendar requires exactly one interval or cron expression")
        return self


class TriggerView(FlowValue):
    id: UUID
    revision_id: UUID
    configuration_id: UUID | None
    kind: str
    revision: int
    status: str
    configuration: TriggerConfiguration


class TriggerControl(FlowValue):
    expected_revision: int = Field(ge=1)
    status: Literal["enabled", "disabled"]


class TriggerAccepted(FlowValue):
    status: str
    flow_run_id: UUID


class TriggerWork(FlowValue):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "TriggerWorkflow"
    trigger_id: UUID
    revision: int
    action: Literal["sync", "fire"] = "sync"


class SourceEventWork(FlowValue):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "SourceEventWorkflow"
    source_kind: Literal["extension", "run", "platform"]
    source_id: UUID
    source_version: str = ""
    after_subscription: UUID | None = None
    accepted: int = Field(default=0, ge=0)
