"""Resolve only attachments admitted into the current event."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace

import anyio

from .models import Attachment, AttachmentError, AttachmentSettings

AttachmentLoader = Callable[[], Awaitable[bytes]]
AttachmentReader = Callable[[str], Awaitable[bytes]]


class AttachmentCollection:
    """Own lazy sources and bounded bytes for one authenticated event."""

    def __init__(self, settings: AttachmentSettings | None = None) -> None:
        """Create an empty collection with bounded lazy sources."""
        self.settings = settings or AttachmentSettings()
        self._metadata: dict[str, Attachment] = {}
        self._loaders: dict[str, AttachmentLoader] = {}
        self._content: dict[str, bytes] = {}
        self._received: set[str] = set()
        self._lock = anyio.Lock()

    def add(self, attachment: Attachment, loader: AttachmentLoader) -> None:
        """Admit a trusted protocol source without starting network I/O."""
        if len(self._received) >= self.settings.max_files:
            raise AttachmentError("Too many attachments")
        self._register(attachment, loader)
        self._received.add(attachment.id)

    def _register(
        self, attachment: Attachment, loader: AttachmentLoader
    ) -> None:
        if attachment.size is not None:
            self._check_size(attachment.size)
        if attachment.id in self._metadata:
            raise AttachmentError("Duplicate attachment identifier")
        self._metadata[attachment.id] = attachment
        self._loaders[attachment.id] = loader

    def add_bytes(self, attachment: Attachment, content: bytes) -> Attachment:
        """Add plugin output without consuming the incoming message budget."""
        self._check_size(len(content))
        generated = self._metadata.keys() - self._received
        if len(generated) >= self.settings.max_files:
            raise AttachmentError("Too many attachments")
        self._check_total(generated, len(content))
        attachment = replace(attachment, size=len(content))

        async def read() -> bytes:
            return content

        self._register(attachment, read)
        self._content[attachment.id] = content
        return attachment

    def receive_bytes(
        self, attachment: Attachment, content: bytes
    ) -> Attachment:
        """Pin an authenticated upload within the incoming message budget."""
        self._check_size(len(content))
        self._check_total(self._received, len(content))
        attachment = replace(attachment, size=len(content))

        async def read() -> bytes:
            return content

        self.add(attachment, read)
        self._content[attachment.id] = content
        return attachment

    def validate_message(self, attachment_ids: Iterable[str]) -> None:
        """Bound all input and output files referenced by one reply."""
        identifiers = set(attachment_ids)
        if len(identifiers) > self.settings.max_files:
            raise AttachmentError("Too many attachments")
        for identifier in identifiers:
            self.get(identifier)
            if identifier not in self._content:
                raise AttachmentError("Message attachments must be resolved")
        self._check_total(identifiers)

    def _check_total(
        self, identifiers: Iterable[str], added_size: int = 0
    ) -> None:
        total = sum(len(self._content.get(key, b"")) for key in identifiers)
        if total + added_size > self.settings.max_message_bytes:
            raise AttachmentError("Message attachments are too large")

    def __contains__(self, attachment_id: str) -> bool:
        """Test whether this event already owns the reference."""
        return attachment_id in self._metadata

    def get(self, attachment_id: str) -> Attachment:
        """Resolve an event-local ID without accepting arbitrary paths."""
        try:
            return self._metadata[attachment_id]
        except KeyError:
            raise AttachmentError("Attachment not available") from None

    async def read(self, attachment_id: str) -> bytes:
        """Read once under per-file, total-size, and timeout limits."""
        self.get(attachment_id)
        async with self._lock:
            if attachment_id not in self._content:
                try:
                    with anyio.fail_after(self.settings.download_timeout):
                        content = await self._loaders[attachment_id]()
                except TimeoutError:
                    raise AttachmentError(
                        "Attachment download timed out"
                    ) from None
                self._check_size(len(content))
                self._check_total(self._received, len(content))
                self._content[attachment_id] = content
            return self._content[attachment_id]

    def _check_size(self, size: int) -> None:
        if size > self.settings.max_file_bytes:
            raise AttachmentError("Attachment is too large")
