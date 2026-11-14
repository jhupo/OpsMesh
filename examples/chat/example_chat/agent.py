"""Construct the native agent from the active plugin configuration."""

from agents import Agent, ModelSettings

from opsmesh import on_startup
from opsmesh.plugins.config import get_plugin_config

from . import channels
from .config import ChatSettings

settings: ChatSettings
agent: Agent[channels.UserContext]


@on_startup
async def startup() -> None:
    """Construct model resources only when the plugin starts."""
    global settings, agent
    settings = get_plugin_config(ChatSettings)
    agent = Agent[channels.UserContext](
        name=settings.name,
        instructions=settings.instructions,
        model=settings.model,
        model_settings=ModelSettings(reasoning=settings.reasoning),
    )
