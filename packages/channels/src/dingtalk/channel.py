"""DingTalk connection state and native stream capabilities."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..channel import MESSAGE_OPERATIONS, CallbackChannel
from .client import DingTalkClient
from .config import DingTalkConnectionSettings
from .errors import DingTalkConfigurationError
from .groups import DingTalkGroups

if TYPE_CHECKING:
    from .stream import DingTalkStream


class DingTalkChannel(CallbackChannel[DingTalkConnectionSettings]):
    """One declared DingTalk robot connection."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types | {"markdown"}
    supported_operations = DingTalkGroups.operations | MESSAGE_OPERATIONS

    @property
    def stream_formats(self) -> frozenset[str]:
        """Return formats with native incremental delivery configured."""
        return frozenset({"markdown"}) if self.settings.card else frozenset()

    def _read_secret(self) -> str:
        reference = self.settings.client_secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise DingTalkConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    def _open_resources(self) -> None:
        self._client = DingTalkClient(self.settings, self._secret)

    async def _close_resources(self) -> None:
        await self.client.close()

    def _create_inbound_task(self) -> DingTalkStream:
        from .stream import DingTalkStream

        return DingTalkStream(self, self._secret)
