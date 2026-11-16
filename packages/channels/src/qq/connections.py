"""QQ Bot API v2 channel connection declarations."""

from __future__ import annotations

import os
from typing import Any

from opsmesh.drivers import get_driver

from ..lifecycle.hooks import transports
from .adapter import QQAdapter
from .channel import (
    QQChannel,
)
from .config import QQConnectionSettings


def _prepare() -> QQAdapter:
    """Resolve the driver-owned adapter without opening network resources."""
    driver = get_driver()
    try:
        return driver.get_adapter(QQAdapter)
    except KeyError:
        from .config import get_settings

        return driver.register_adapter(QQAdapter, settings=get_settings())


def get_adapter() -> QQAdapter:
    """Return the running adapter registered with the framework driver."""
    adapter = get_driver().get_adapter(QQAdapter)
    if not adapter.started:
        raise RuntimeError("The qq channel is not running")
    return adapter


def _declare_connection(
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
    if (
        adapter.settings.enabled
        and not os.environ.get(settings.secret_ref, "").strip()
    ):
        raise ValueError(
            f"qq.{name}: missing credential environment variable "
            f"{settings.secret_ref}"
        )
    for declared in adapter.declarations:
        if declared.settings.app_id == settings.app_id:
            raise ValueError(
                f"qq.{name}: robot account already belongs to "
                f"{declared.owner_id}"
            )
    channel = adapter.declare(settings, name=name, owner=owner)
    transports.use(adapter, owner)
    return channel
