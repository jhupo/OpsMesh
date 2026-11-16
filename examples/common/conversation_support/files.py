"""Optional ordered file selection for one caller-owned response."""

from dataclasses import dataclass
from hashlib import sha256

from opsmesh.plugins.channels.attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
)
from opsmesh.plugins.channels.messages import Message, MessageSegment


@dataclass(frozen=True)
class Deliverable:
    """An immutable file reference selected for delivery."""

    attachment: Attachment
    checksum: str
    description: str = ""


class Deliverables:
    """Reuse attachment storage without knowing models or tool identities."""

    def __init__(self, attachments: AttachmentCollection) -> None:
        """Borrow the event attachment collection without copying bytes."""
        self.attachments = attachments
        self._fixed: dict[str, Deliverable] = {}
        self._selected: list[Deliverable] = []

    def add(self, attachment: Attachment, content: bytes) -> Attachment:
        """Fix actual bytes before returning a usable output reference."""
        if not isinstance(content, bytes) or not content:
            raise AttachmentError(
                "Deliverables require nonempty immutable bytes"
            )
        attachment = self.attachments.add_bytes(attachment, content)
        self._fixed[attachment.id] = Deliverable(
            attachment, sha256(content).hexdigest()
        )
        return attachment

    def get(self, identifier: str) -> Deliverable:
        """Resolve only files fixed by this invocation's collection."""
        try:
            return self._fixed[identifier]
        except KeyError:
            raise AttachmentError("Deliverable is not available") from None

    def select(self, identifier: str, *, description: str = "") -> None:
        """Select a fixed file; repeated selection is an explicit repeat."""
        item = self.get(identifier)
        self._selected.append(
            Deliverable(item.attachment, item.checksum, description)
        )

    @property
    def entries(self) -> tuple[Deliverable, ...]:
        """Return an immutable snapshot of the selected file order."""
        return tuple(self._selected)

    def message(self) -> Message:
        """Produce references without granting download access or sending."""
        parts = Message()
        for item in self._selected:
            if item.description:
                parts.append(MessageSegment.text(item.description))
            parts.append(MessageSegment.attachment(item.attachment))
        return parts
