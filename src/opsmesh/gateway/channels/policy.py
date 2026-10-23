"""Transport-independent admission, group visibility and delivery constraints."""

from typing import Literal

from opsmesh_sdk.channels import InboundMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visibility: Literal["all", "mention_only"] = "mention_only"
    activation: Literal["mention", "always"] = "mention"
    session_scope: Literal["user", "group"] = "group"
    allow_recall: bool = False
    control_roles: frozenset[str] = frozenset({"owner", "admin"})

    @model_validator(mode="after")
    def validate_activation(self) -> "GroupPolicy":
        if self.activation == "always" and self.visibility != "all":
            raise ValueError("Always activation requires explicitly granted all-message visibility")
        return self


class GatewayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    private_enabled: bool = True
    group: GroupPolicy = Field(default_factory=GroupPolicy)
    groups: dict[str, GroupPolicy] = Field(default_factory=dict, max_length=1000)
    workers: int = Field(default=4, ge=1, le=32)
    debounce_seconds: float = Field(default=0, ge=0, le=10)
    batch_max_wait_seconds: float = Field(default=2, gt=0, le=30)
    batch_max_messages: int = Field(default=20, ge=1, le=100)
    message_filters: list[str] = Field(default_factory=list, max_length=20)
    filter_max_attempts: int = Field(default=3, ge=1, le=10)
    filter_retry_seconds: float = Field(default=1, gt=0, le=60)
    send_limit: int = Field(default=20, ge=1, le=1000)
    send_window_seconds: int = Field(default=60, ge=1, le=3600)
    send_timeout_seconds: int = Field(default=30, ge=1, le=300)
    response_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    progress_after_seconds: int = Field(default=0, ge=0, le=300)
    progress_text: str = Field(default="Processing your request.", min_length=1, max_length=500)
    show_tool_hints: bool = False
    tool_hint_throttle_seconds: int = Field(default=6, ge=0, le=300)
    show_thinking: bool = False
    streaming: bool = False
    typing_interval_seconds: int = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def validate_filters(self) -> "GatewayPolicy":
        if len(set(self.message_filters)) != len(self.message_filters):
            raise ValueError("Message filter bindings must be unique")
        if any(not value or len(value) > 200 for value in self.message_filters):
            raise ValueError("Invalid message filter resource ID")
        return self

    def group_for(self, message: InboundMessage) -> GroupPolicy:
        return self.groups.get(message.conversation.external_id, self.group)

    def activates(self, message: InboundMessage) -> bool:
        if message.conversation.kind == "private":
            return self.private_enabled
        policy = self.group_for(message)
        return message.context.bot_mentioned or (
            policy.activation == "always"
            and policy.visibility == "all"
            and message.context.visibility == "all"
        )
