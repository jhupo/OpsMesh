"""Ordinary typed functions, without a platform-specific argument envelope."""

from opsmesh_sdk import get_tool_config


def echo_prefix(message: str) -> str:
    """Return a message with the configured prefix."""
    return str(get_tool_config().get("prefix", "")) + message
