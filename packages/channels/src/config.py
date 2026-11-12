"""Read opaque protocol sections from the channels configuration."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from opsmesh.plugins.config import get_plugin_config


class ChannelsSettings(BaseModel):
    """Keep protocol sections unparsed until a declared entrance needs them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api: Any = None
    qq: Any = None
    dingtalk: Any = None


def protocol_settings(name: str) -> Any:
    """Return one section; its protocol owns validation and defaults."""
    settings = get_plugin_config(ChannelsSettings)
    return getattr(settings, name)
