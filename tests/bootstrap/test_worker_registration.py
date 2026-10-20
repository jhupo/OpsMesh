"""The feature worker registers every workflow a delivered step stages.

A service that stages a durable execution whose workflow is not
registered here would wait forever, so the registration set is asserted
as data: every delivered step's workflow and activity must be present.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from opsmesh.bootstrap.worker import feature_registrations
from opsmesh.platform.model_connections.workflow import ModelProviderProbeWorkflow
from opsmesh.shared.security.encryption import SecretCipher

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


def registrations():
    return feature_registrations(
        async_sessionmaker(),
        cipher=SecretCipher(key=TEST_KEY, key_id="test"),
    )


def test_the_registration_set_carries_every_delivered_workflow() -> None:
    assembled = registrations()
    assert set(assembled.workflows) == {
        ModelProviderProbeWorkflow,
    }


def test_the_registration_set_carries_the_probe_activity() -> None:
    assembled = registrations()
    names = [activity.__name__ for activity in assembled.activities]
    assert names == [
        "probe_connection",
    ]
