"""Contract checks for channel API boundaries; vendor protocols stay out of core."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.channels.schemas import ChannelAccountCreate, ChannelReceiveRequest


@pytest.mark.parametrize("removed_field", ["workspace_id", "project_id"])
def test_account_rejects_removed_scope_fields(removed_field: str) -> None:
    with pytest.raises(ValidationError):
        ChannelAccountCreate.model_validate(
            {
                removed_field: uuid4(),
                "channel_type": "telegram",
                "external_account_id": "bot-1",
                "installation_id": uuid4(),
                "version_id": uuid4(),
            }
        )


def test_account_accepts_configuration_scope() -> None:
    identifier = uuid4()
    account = ChannelAccountCreate(
        configuration_id=identifier,
        channel_type="telegram",
        external_account_id="bot-1",
        installation_id=uuid4(),
        version_id=uuid4(),
    )
    assert account.configuration_id == identifier


def test_receive_contract_bounds_and_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChannelReceiveRequest(
            epoch=1,
            external_message_id="message-1",
            conversation_key="chat-1",
            payload={},
            signature="vendor-signature",
        )
