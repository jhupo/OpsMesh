"""Minimal consumer plugin using framework-owned file configuration."""

from pydantic import BaseModel

from opsmesh import get_plugin_config
from opsmesh.plugin import PluginMetadata


class ConsumerConfig(BaseModel):
    model: str = "example-model"
    greeting: str = "hello"


__plugin_meta__ = PluginMetadata(
    name="Example consumer",
    description="A minimal plugin with file configuration and named adapter bindings.",
    usage="Use settings.json for plugin config and QQ/DingTalk dependencies.",
    type="example",
    config=ConsumerConfig,
    config_file="settings.json",
)


def get_settings() -> ConsumerConfig:
    """Return this consumer's validated configuration snapshot."""
    return get_plugin_config(ConsumerConfig)
