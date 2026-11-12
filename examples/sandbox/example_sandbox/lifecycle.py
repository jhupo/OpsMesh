"""Bind database and filesystem dependencies after schema validation."""

from opsmesh.framework import on_shutdown, on_startup

from . import database
from .sessions import SandboxConversations
from .workspace import SandboxWorkspaces

_conversations: SandboxConversations | None = None


def get_conversations() -> SandboxConversations:
    """Return the started conversation owner."""
    if _conversations is None:
        raise RuntimeError("Sandbox conversations have not started")
    return _conversations


@on_startup
async def startup() -> None:
    """Configure the trusted Linux backend without creating a workspace."""
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

    global _conversations
    client = UnixLocalSandboxClient(inherit_host_environment=False)
    _conversations = SandboxConversations(
        database.get_engine(),
        SandboxWorkspaces(client),
    )


@on_shutdown
async def shutdown() -> None:
    """Forget handles after channels have drained all workspace scopes."""
    global _conversations
    _conversations = None
