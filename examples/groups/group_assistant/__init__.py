"""One robot connection with code-defined group-to-agent selection."""

from opsmesh import require
from opsmesh.plugins import PluginMetadata

channels = require("channels")
execution = require("execution")

from . import agents, handlers  # noqa: E402, F401
from .config import AgentSettings  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="Group assistants",
    description="Route group messages to native agents using framework rules",
    usage="Configure a robot named main and set group IDs in handlers.py.",
    config=AgentSettings,
)
