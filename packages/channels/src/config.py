"""Dependency configuration for the connections of one consumer plugin."""

import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)

from .api.config import APIConnectionSettings
from .dingtalk.config import DingTalkConnectionSettings
from .qq.config import QQConnectionSettings

_CONNECTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_connection_name(name: str) -> str:
    """Validate a name used inside a channel identifier."""
    if not isinstance(name, str) or not _CONNECTION_NAME.fullmatch(name):
        raise ValueError("Connection names must be 1-64 URL-safe characters")
    if ".." in name:
        raise ValueError("Connection names must not contain '..'")
    return name


class ChannelConnections(BaseModel):
    """Named HTTP listeners and robot accounts declared by one plugin."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    api: dict[str, APIConnectionSettings] = Field(default_factory=dict)
    qq: dict[str, QQConnectionSettings] = Field(default_factory=dict)
    dingtalk: dict[str, DingTalkConnectionSettings] = Field(
        default_factory=dict
    )

    @field_validator("api", "qq", "dingtalk", mode="before")
    @classmethod
    def validate_names(cls, value: Any, info: ValidationInfo) -> Any:
        """Reject names that cannot appear in a channel identifier."""
        if not isinstance(value, dict):
            raise ValueError(
                "Connections must be a mapping of names to settings"
            )
        for name in value:
            validate_connection_name(name)
        return value
