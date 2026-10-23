"""Transport-independent admission, group visibility and delivery constraints."""

from typing import Literal

from opsmesh_sdk.channels import InboundMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visibility: Literal["auto", "all", "mention_recent", "mention_only"] = "auto"
    activation: Literal["mention", "always"] = "mention"
    history_limit: int = Field(default=10, ge=0, le=100)
    history_ttl_seconds: int = Field(default=300, ge=0, le=3600)
    history_scope: Literal["user", "group"] = "user"
    clear_after_reply: bool = True

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

    def prepare_context(self, message: InboundMessage, now: float) -> InboundMessage:
        policy = self.group_for(message)
        recent = []
        if (
            message.conversation.kind != "private"
            and policy.visibility != "mention_only"
            and policy.history_limit
            and policy.history_ttl_seconds
        ):
            seen = {message.event_id}
            for item in sorted(message.context.recent_messages, key=lambda item: item.timestamp):
                if (
                    item.message_id not in seen
                    and now - policy.history_ttl_seconds <= item.timestamp <= now
                    and (
                        policy.history_scope == "group"
                        or item.sender_id == message.actor.external_id
                    )
                ):
                    recent.append(item)
                    seen.add(item.message_id)
            recent = recent[-policy.history_limit :] if policy.history_limit else []
        return message.model_copy(
            update={"context": message.context.model_copy(update={"recent_messages": recent})}
        )
