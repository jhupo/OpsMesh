"""DingTalk connection state and outgoing message rules."""

from __future__ import annotations

from typing import Any

from ..channel import RobotChannel
from .config import DingTalkConnectionSettings, DingTalkSettings
from .events import DingTalkMessageEvent


class DingTalkChannel(
    RobotChannel[DingTalkConnectionSettings, DingTalkSettings]
):
    """One declared DingTalk robot connection."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types | {"markdown"}

    @property
    def stream_formats(self) -> frozenset[str]:
        """Return formats with native incremental delivery configured."""
        return frozenset({"markdown"}) if self.settings.card else frozenset()

    @property
    def supported_operations(self) -> frozenset[str]:
        """Return implemented operations, independent of account privileges."""
        from .groups import DingTalkGroups

        return DingTalkGroups.operations | {"send_message", "stream"}

    async def send(
        self,
        event: DingTalkMessageEvent,
        message: Any,
        **kwargs: Any,
    ) -> Any:
        """Reply to one incoming DingTalk message.

        Args:
            event: The incoming DingTalk message event.
            message: Text or framework message with attachment references.
            **kwargs: Optional delivery observer.

        Returns:
            The vendor response payload.
        """
        if not isinstance(event, DingTalkMessageEvent):
            raise TypeError(
                "DingTalkChannel.send requires a DingTalkMessageEvent"
            )
        return await self.call(
            "send_message", event=event, message=message, **kwargs
        )
