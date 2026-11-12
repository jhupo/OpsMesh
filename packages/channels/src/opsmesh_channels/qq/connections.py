"""QQ official bot channel with verified webhook callbacks."""

from __future__ import annotations

from typing import Any

from opsmesh.framework import runtime

from ..lifecycle import transports
from .adapter import QQAdapter
from .channel import (
    QQChannel,
)
from .config import QQConnectionSettings

_adapter: QQAdapter | None = None


def _prepare() -> QQAdapter:
    """Validate QQ settings and construct the adapter without I/O."""
    global _adapter
    if _adapter is None:
        from .config import get_settings

        settings = get_settings()
        _adapter = QQAdapter(runtime.get_driver(), settings=settings)
    return _adapter


def get_adapter() -> QQAdapter:
    """Return the running QQ adapter."""
    if _adapter is None or not _adapter.started:
        raise RuntimeError("The QQ channel is not running")
    return _adapter


def declare_connection(
    settings: QQConnectionSettings,
    *,
    name: str,
    owner: Any = None,
) -> QQChannel:
    """Declare a bot connection owned by the calling plugin.

    Args:
        settings: The validated protocol settings for this account.
        name: The connection name unique within the owning plugin.
        owner: Optional module or object identifying the declaring plugin.

    Returns:
        The declared connection handle.
    """
    adapter = _prepare()
    channel = adapter.declare(settings, name=name, owner=owner)
    transports.use(adapter, owner)
    return channel
