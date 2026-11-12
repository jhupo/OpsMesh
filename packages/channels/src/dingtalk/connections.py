"""DingTalk robot channel with official Stream SDK connections."""

from __future__ import annotations

from typing import Any

from opsmesh.drivers import get_driver

from ..lifecycle.coordinator import channel_lifecycle
from .adapter import DingTalkAdapter
from .channel import (
    DingTalkChannel,
)
from .config import (
    DingTalkConnectionSettings,
)


def _prepare() -> DingTalkAdapter:
    """Resolve the driver-owned adapter without opening network resources."""
    driver = get_driver()
    try:
        return driver.get_adapter(DingTalkAdapter)
    except KeyError:
        from .config import get_settings

        return driver.register_adapter(
            DingTalkAdapter,
            settings=get_settings(),
        )


def get_adapter() -> DingTalkAdapter:
    """Return the running adapter registered with the framework driver."""
    adapter = get_driver().get_adapter(DingTalkAdapter)
    if not adapter.started:
        raise RuntimeError("The dingtalk channel is not running")
    return adapter


def declare_connection(
    settings: DingTalkConnectionSettings,
    *,
    name: str,
    owner: Any = None,
) -> DingTalkChannel:
    """Declare a robot connection owned by the calling plugin.

    Args:
        settings: The validated protocol settings for this account.
        name: The connection name unique within the owning plugin.
        owner: Optional module or object identifying the declaring plugin.

    Returns:
        The declared connection handle.
    """
    adapter = _prepare()
    channel = adapter.declare(settings, name=name, owner=owner)
    channel_lifecycle.transports.use(adapter, owner)
    return channel
