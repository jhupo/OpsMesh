"""Inject already-declared connections without constructing adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opsmesh.framework.adapters import Channel
from opsmesh.framework.params import Depends

if TYPE_CHECKING:
    from .dingtalk.channel import DingTalkChannel
    from .qq.channel import QQChannel


def Connection(connection: QQChannel | DingTalkChannel) -> Any:
    """Inject a declared connection after checking its lifecycle state.

    Use the framework's Channel parameter for the incoming connection. This
    descriptor selects an explicit handle for outbound or cross-channel work.
    """

    def provide() -> Channel:
        if not connection.ready or not connection.owner_ready:
            raise RuntimeError("The declared channel connection is not ready")
        return connection

    return Depends(provide)
