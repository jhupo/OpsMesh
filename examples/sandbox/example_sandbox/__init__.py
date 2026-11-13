"""Declare a persistent native SandboxAgent conversation plugin."""

from pathlib import Path

from opsmesh.plugins import PluginMetadata, require

channels = require("channels")
execution = require("execution")
database = require("database")
filesystem = require("filesystem")
conversations = require("conversation_support")

from . import handlers, messages  # noqa: E402, F401
from .config import SandboxSettings  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="Sandbox conversation",
    description="Native SandboxAgent sessions with persistent owned workspaces",
    usage="Use session endpoints and POST /plugins/example_sandbox/chat.",
    type="agent",
    config=SandboxSettings,
    extra={
        "database": database.DatabaseSchema(
            Path(__file__).with_name("migrations"),
            "0001_example_sandbox",
        )
    },
)
