"""Mutable composition state is separated from public immutable revisions."""

from uuid import uuid4

import pytest

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.definitions.models import Definition
from opsmesh.orchestration.definitions.routes import definition_view
from opsmesh.orchestration.definitions.service import load_definition
from opsmesh.shared.errors import NotFoundError


class SessionStub:
    def __init__(self, record: Definition) -> None:
        self.record = record

    def get(self, model: type[Definition], definition_id, **kwargs):  # noqa: ANN001
        return self.record if definition_id == self.record.id else None

    def scalar(self, statement):  # noqa: ANN001
        return None


def user(user_id):  # noqa: ANN001
    return CurrentUser(
        user_id=user_id,
        email="user@example.test",
        display_name="User",
        status="active",
        platform_roles=frozenset(),
    )


def definition(*, owner_id, published_number):
    return Definition(
        id=uuid4(),
        owner_id=owner_id,
        name="Public analyst",
        kind="agent_graph",
        visibility="public",
        status="active",
        revision=2,
        published_number=published_number,
        draft={"entry_agent": "main", "agents": {"main": {"name": "Draft"}}},
    )


def test_public_reader_cannot_read_mutable_draft():
    owner_id, reader_id = uuid4(), uuid4()
    record = definition(owner_id=owner_id, published_number=1)
    session = SessionStub(record)

    loaded = load_definition(session, actor=user(reader_id), definition_id=record.id)

    assert definition_view(loaded, session=session, user=user(reader_id)).draft is None


def test_public_definition_without_published_revision_is_not_readable():
    owner_id, reader_id = uuid4(), uuid4()
    record = definition(owner_id=owner_id, published_number=0)

    with pytest.raises(NotFoundError):
        load_definition(SessionStub(record), actor=user(reader_id), definition_id=record.id)
