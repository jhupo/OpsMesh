"""Attachment references retain resource authorization and storage accounting."""

import pytest

from opsmesh.channels.attachments import authorize_attachments
from opsmesh.channels.schemas import ChannelAttachment
from opsmesh.resources.files import service
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError
from tests.resources.test_resources_postgres import (
    actor_of,
    ledger,
    make_file,
    open_account,
    reader_of,
)


def test_attachment_grant_revocation_and_size_limits(database_sessions, tmp_path):
    owner = open_account(database_sessions, email="attachment@example.com", display_name="Owner")
    recipient = open_account(
        database_sessions, email="attachment-reader@example.com", display_name="Reader"
    )
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session, session.begin():
        service.upload_version(
            session,
            file_id=resource.id,
            content=b"small",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="small",
        )
        grant = service.grant_access(
            session,
            file_id=resource.id,
            grantee_id=recipient.id,
            permissions=["read"],
            reader=reader_of(owner),
            actor=actor_of(owner),
        )
        grant_id = grant.id
    reference = ChannelAttachment(file_id=resource.id, version=1)
    with database_sessions() as session, session.begin():
        authorize_attachments(session, user_id=recipient.id, attachments=[reference])
        service.revoke_access(
            session, file_id=resource.id, grant_id=grant_id, reader=reader_of(owner)
        )
    with database_sessions() as session, pytest.raises(ForbiddenError):
        authorize_attachments(session, user_id=recipient.id, attachments=[reference])
    with database_sessions() as session, session.begin():
        service.upload_version(
            session,
            file_id=resource.id,
            content=b"x" * (16 * 1_048_576 + 1),
            media_type="application/octet-stream",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="large",
        )
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        authorize_attachments(
            session,
            user_id=owner.id,
            attachments=[ChannelAttachment(file_id=resource.id, version=2)],
        )
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        authorize_attachments(session, user_id=owner.id, attachments=[reference] * 11)
