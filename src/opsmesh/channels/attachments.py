"""Channels reference quota-accounted immutable resource versions, never vendor URLs."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.channels.schemas import ChannelAttachment
from opsmesh.resources.files.access import ResourceReader
from opsmesh.resources.files.service import authorize_version_reference
from opsmesh.shared.errors import InvalidArgumentError


def authorize_attachments(
    session: Session, *, user_id: UUID, attachments: list[ChannelAttachment]
) -> None:
    if len(attachments) > 10:
        raise InvalidArgumentError("A channel message accepts at most ten attachments")
    total = 0
    for attachment in attachments:
        version = authorize_version_reference(
            session,
            file_id=attachment.file_id,
            version_number=attachment.version,
            reader=ResourceReader(user_id),
        )
        total += version.size
        if version.size > 16 * 1_048_576 or total > 32 * 1_048_576:
            raise InvalidArgumentError("Channel attachments exceed the permitted size")
