"""Declare the native agent independently of transport and execution."""

from agents import ModelSettings
from agents.sandbox import SandboxAgent

from opsmesh import on_startup
from opsmesh.plugins.config import get_plugin_config

from . import channels
from .config import SandboxSettings

settings: SandboxSettings
agent: SandboxAgent[channels.UserContext]


@on_startup
async def startup() -> None:
    """Construct the native sandbox agent from current configuration."""
    global settings, agent
    settings = get_plugin_config(SandboxSettings)
    agent = SandboxAgent[channels.UserContext](
        name="Sandbox assistant",
        instructions=(
            "Use your workspace to solve the task and explain the result."
        ),
        model=settings.model,
        model_settings=ModelSettings(reasoning=settings.reasoning),
    )
