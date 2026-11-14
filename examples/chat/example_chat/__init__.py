"""Load the chat plugin's dependencies and declare its entrances."""

from pathlib import Path

from opsmesh.plugins import PluginMetadata, require

channels = require("channels")
execution = require("execution")
database = require("database")
conversations = require("conversation_support")

# Import declarations only after their dependency plugins are loaded.
from . import (  # noqa: E402, F401
    callbacks,
    connections,
    handlers,
)
from .config import ChatSettings  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="OpsMesh Chat Agent",
    description="A native SDK agent with HTTP and bot command entrances",
    usage="POST /plugins/example_chat/messages; use conversation commands.",
    type="agent",
    config=ChatSettings,
    extra={
        "database": database.DatabaseSchema(
            Path(__file__).with_name("migrations"),
            "0001_example_chat",
        )
    },
)
