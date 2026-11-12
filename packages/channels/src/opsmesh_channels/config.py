"""Read opaque protocol sections from the channels configuration."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from opsmesh.framework.plugins.config import get_plugin_config

CONFIG_NAMESPACE = "opsmesh_channels"


class ChannelsSettings(BaseModel):
    """Keep protocol sections unparsed until a declared entrance needs them."""

    model_config = ConfigDict(extra="allow", frozen=True)


def protocol_settings(name: str) -> Any:
    """Return one section; its protocol owns validation and defaults."""
    settings = get_plugin_config(ChannelsSettings, namespace=CONFIG_NAMESPACE)
    return (settings.model_extra or {}).get(name)
