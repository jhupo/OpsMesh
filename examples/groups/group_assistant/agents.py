"""Construct native agents when the example plugin starts."""

from agents import Agent

from opsmesh import get_plugin_config, on_startup
from opsmesh.plugins.channels import UserContext

from .config import AgentSettings

sales: Agent[UserContext]
technical: Agent[UserContext]


@on_startup
def prepare() -> None:
    """Apply current model settings on each plugin activation."""
    global sales, technical
    settings = get_plugin_config(AgentSettings)
    sales = Agent(
        name="Sales assistant",
        instructions="Answer product and sales questions.",
        model=settings.model,
    )
    technical = Agent(
        name="Technical assistant",
        instructions="Help users diagnose and resolve technical issues.",
        model=settings.model,
    )
