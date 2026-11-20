import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from opsmesh.compat import (
    PYDANTIC_V2,
    ConfigDict,
    field_validator,
    type_validate_python,
)


class Intents(BaseModel):
    guilds: bool = True
    guild_members: bool = True
    guild_messages: bool = False
    guild_message_reactions: bool = True
    direct_message: bool = False
    open_forum_event: bool = False
    audio_live_member: bool = False
    group_members: bool = False
    c2c_group_at_messages: bool = False
    interaction: bool = False
    message_audit: bool = True
    forum_event: bool = False
    audio_action: bool = False
    at_messages: bool = True

    if PYDANTIC_V2:
        model_config: ConfigDict = ConfigDict(extra="forbid")
    else:

        class Config:
            extra = "forbid"

    def to_int(self):
        return (
            self.guilds << 0
            | self.guild_members << 1
            | self.guild_messages << 9
            | self.guild_message_reactions << 10
            | self.direct_message << 12
            | self.open_forum_event << 18
            | self.audio_live_member << 19
            | self.group_members << 24
            | self.c2c_group_at_messages << 25
            | self.interaction << 26
            | self.message_audit << 27
            | self.forum_event << 28
            | self.audio_action << 29
            | self.at_messages << 30
        )


class Account(BaseModel):
    app_id: str
    secret_ref: str
    shard: tuple[int, int] | None = None
    intent: Intents = Field(default_factory=Intents)
    transport: Literal["websocket", "webhook"] = "websocket"

    if PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")
    else:

        class Config:
            extra = "forbid"

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, value: str) -> str:
        if (
            not 1 <= len(value) <= 32
            or not value.isascii()
            or not value.isdecimal()
        ):
            raise ValueError("QQ app_id must contain 1 to 32 ASCII digits")
        return value

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, value: str) -> str:
        if not value.isascii() or not value.isidentifier():
            raise ValueError(
                "QQ secret_ref must be an environment variable name"
            )
        return value

    @field_validator("shard")
    @classmethod
    def validate_shard(
        cls, value: tuple[int, int] | None
    ) -> tuple[int, int] | None:
        if value is not None and not 0 <= value[0] < value[1]:
            raise ValueError(
                "QQ shard must be [index, total] with 0 <= index < total"
            )
        return value

    def read_secret(self) -> str:
        if not (secret := os.environ.get(self.secret_ref, "").strip()):
            raise ValueError(
                f"QQ environment variable {self.secret_ref} is unset"
            )
        return secret


class Settings(BaseModel):
    accounts: list[Account] = Field(default_factory=list)
    if PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")
    else:

        class Config:
            extra = "forbid"

    @field_validator("accounts")
    @classmethod
    def validate_accounts(cls, value: list[Account]) -> list[Account]:
        if len({account.app_id for account in value}) != len(value):
            raise ValueError("Duplicate QQ app_id")
        if any(
            a.shard is not None and a.transport != "websocket" for a in value
        ):
            raise ValueError("QQ shard is only valid for WebSocket accounts")
        return value


class Config(BaseModel):
    plugin_settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("plugin_settings")
    @classmethod
    def validate_qq(cls, value: dict[str, Any]) -> dict[str, Any]:
        type_validate_python(Settings, value.get("qq", {}))
        return value

    @property
    def qq(self) -> Settings:
        return type_validate_python(
            Settings, self.plugin_settings.get("qq", {})
        )
