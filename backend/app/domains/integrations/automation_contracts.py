"""Configuration and event admission contracts for connector-backed automations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from opsmesh_plugin_sdk.messaging.contracts import AttachmentKind, MessageAction
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domains.capabilities.resources.schema import (
    reject_embedded_secrets,
    validate_json_schema,
)
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowDataBinding
from backend.app.runtime.workers.scheduling.calendar import next_run_at


class ExternalIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    active: bool = True


class ExternalIdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    automation_id: UUID
    sender_id: str
    user_id: UUID
    status: str


class AutomationConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    orchestration_definition_id: UUID
    orchestration_version: int = Field(ge=1)
    agent_team_id: UUID
    workspace_project_id: UUID | None = None
    input_defaults: dict[str, object] = Field(default_factory=dict, max_length=64)
    trigger_type: Literal["message", "schedule"]
    allowed_senders: list[str] = Field(default_factory=list, max_length=256)
    reply_subscription_id: UUID | None = None
    schedule_type: Literal["one_shot", "hourly", "daily"] | None = None
    schedule_config: dict[str, object] = Field(default_factory=dict, max_length=4)
    overlap_policy: Literal["queue", "skip"] = "queue"
    allowed_message_actions: list[MessageAction] = Field(
        default=["start"], min_length=1, max_length=6
    )
    notify_progress: bool = False
    contract_version: int = Field(default=1, ge=1)
    input_schema: dict[str, object] = Field(default={"type": "object"})
    output_schema: dict[str, object] = Field(default={"type": "object"})
    model_input_fields: list[str] = Field(default_factory=list, max_length=64)
    output_binding: WorkflowDataBinding | None = None
    stream_output_nodes: list[str] = Field(default_factory=list, max_length=128)
    stream_tool_events: bool = False
    allowed_attachment_kinds: list[AttachmentKind] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_configuration(self) -> AutomationConfiguration:
        for schema in (self.input_schema, self.output_schema):
            validate_json_schema(schema)
            if schema.get("type") != "object":
                raise ValueError("Automation input/output schemas must declare object type")
        for names in (self.model_input_fields, self.stream_output_nodes):
            if len(set(names)) != len(names) or any(not name.strip() for name in names):
                raise ValueError("Projection fields and stream nodes must be unique nonempty names")
        reject_embedded_secrets(self.input_defaults)
        if len(set(self.allowed_message_actions)) != len(self.allowed_message_actions):
            raise ValueError("Message actions must be unique")
        if self.trigger_type == "message":
            if not self.allowed_senders or any(not item.strip() for item in self.allowed_senders):
                raise ValueError("Message automations require explicit allowed_senders")
            if self.schedule_type is not None or self.schedule_config:
                raise ValueError("Message automations cannot define schedules")
        else:
            if self.allowed_message_actions != ["start"]:
                raise ValueError("Scheduled automations cannot enable message controls")
            if self.schedule_type is None:
                raise ValueError("Scheduled automations require schedule_type")
            if self.allowed_senders:
                raise ValueError("Scheduled automations cannot define allowed_senders")
            from datetime import UTC

            next_run_at(
                schedule_type=self.schedule_type,
                schedule_config=self.schedule_config,
                after=datetime.now(UTC),
                include_now=True,
            )
        return self


class AutomationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    configuration: AutomationConfiguration
    status: Literal["active", "paused"]


class AutomationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    version: int
    status: str
    configuration: AutomationConfiguration
    next_due_at: datetime | None
