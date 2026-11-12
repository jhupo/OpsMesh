"""Declare a persistent native SandboxAgent conversation plugin."""

from pathlib import Path

from opsmesh.framework.plugins import PluginMetadata, require

channels = require("opsmesh_channels")
execution = require("opsmesh_execution")
database = require("opsmesh_database")
filesystem = require("opsmesh_filesystem")
conversations = require("conversation_support")

from . import handlers  # noqa: E402, F401
from .config import SandboxSettings  # noqa: E402
from .models import SandboxBase  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="Sandbox conversation",
    description="Native SandboxAgent sessions with persistent owned workspaces",
    usage="Use session endpoints and POST /plugins/example_sandbox/chat.",
    type="agent",
    config=SandboxSettings,
    extra={
        "database": database.DatabaseSchema(
            SandboxBase.metadata,
            Path(__file__).with_name("migrations"),
            "0001_example_sandbox",
        )
    },
)
