"""Protocol-neutral conversation bindings and bounded operational policies."""

from __future__ import annotations

import hashlib
import string
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_id: str = Field(min_length=1, max_length=512)
    channel_id: str = Field(min_length=1, max_length=1024)
    principal_id: str = Field(min_length=1, max_length=512)
    agent_ref: str = Field(min_length=1, max_length=512)
    workspace_id: str | None = None
    display_name: str = Field(default="", max_length=80)
    proactive_enabled: bool = False
    knowledge_collection: str | None = None
    memory_namespace_id: str | None = None

    @property
    def key(self) -> str:
        # Length-prefixing avoids collisions when opaque identifiers contain colons.
        return f"{len(self.bot_id)}:{self.bot_id}{self.channel_id}"

    @property
    def capture_prefix(self) -> str:
        return "conversation-capture:" + hashlib.sha256(self.key.encode()).hexdigest() + ":"


class ConversationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    poll_seconds: float = Field(default=0.4, ge=0.1, le=10)
    batch_delay_seconds: float = Field(default=1.5, ge=0, le=10)
    moderation_concurrency: int = Field(default=4, ge=1, le=12)
    recall_window_seconds: int = Field(default=110, ge=1, le=3600)
    batch_characters: int = Field(default=12000, ge=1000, le=16000)
    activity_history_limit: int = Field(default=24, ge=1, le=128)
    run_wait_seconds: float = Field(default=180, ge=0.01, le=600)
    reply_character_limit: int = Field(default=700, ge=100, le=1500)
    images_enabled: bool = False
    image_limit: int = Field(default=4, ge=1, le=8)
    image_allowed_hosts: tuple[str, ...] = Field(default=(), max_length=32)
    proactive_interval_seconds: int = Field(default=3600, ge=600)
    proactive_recent_activity_seconds: int = Field(default=1800, ge=60)
    proactive_daily_limit: int = Field(default=4, ge=0, le=12)
    timezone: str = "Asia/Shanghai"
    quiet_start_hour: int = Field(default=23, ge=0, le=23)
    quiet_end_hour: int = Field(default=8, ge=0, le=23)
    retention_days: int = Field(default=7, ge=1, le=90)
    removed_notice: str = ""
    unconfirmed_notice: str = ""

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        ZoneInfo(self.timezone)
        for template in (self.removed_notice, self.unconfirmed_notice):
            # Only the fixed reason field may be interpolated; no attribute access.
            fields = list(string.Formatter().parse(template))
            if any(
                name not in {None, "reason"} or spec or conversion
                for _, name, spec, conversion in fields
            ):
                raise ValueError("Moderation notices support only the {reason} field")
            if len(template) > self.reply_character_limit:
                raise ValueError("Moderation notice exceeds the reply limit")
        return self


class ConversationOptions(ConversationSettings):
    data_directory: Path
    bindings: tuple[ConversationBinding, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if len({item.key for item in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate conversation binding")
        return self

    def binding(self, bot_id: str, channel_id: str) -> ConversationBinding | None:
        return next(
            (
                item
                for item in self.bindings
                if (item.bot_id, item.channel_id) == (bot_id, channel_id)
            ),
            None,
        )
