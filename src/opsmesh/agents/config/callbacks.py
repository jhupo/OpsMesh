"""References resolve only through an application-owned callback registry."""

from typing import Literal

from pydantic import Field, JsonValue

from .base import ConfigValue, Identifier


class CallbackRef(ConfigValue):
    type: Literal["callback"] = "callback"
    callback_id: Identifier
    config: dict[str, JsonValue] = Field(default_factory=dict)
