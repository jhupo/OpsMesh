"""Frozen invocation grants cannot outlive installation or instance permissions."""

import pytest
from pydantic import ValidationError

from opsmesh.extensions.schemas import InstanceConfiguration
from opsmesh.extensions.security import authorize_permissions
from opsmesh.shared.errors import ForbiddenError


def test_current_permission_revocation_blocks_frozen_host_grant():
    with pytest.raises(ForbiddenError):
        authorize_permissions(
            ceiling=["host.state.read"],
            instance=[],
            requested=["host.state.read"],
            permission="host.state.read",
        )


def test_granted_host_permission_must_also_belong_to_the_invocation():
    with pytest.raises(ForbiddenError):
        authorize_permissions(
            ceiling=["host.state.read", "host.state.write"],
            instance=["host.state.read", "host.state.write"],
            requested=["host.state.read"],
            permission="host.state.write",
        )


@pytest.mark.parametrize("requested", [None, "host.state.read", [42], [{}]])
def test_malformed_frozen_permissions_are_rejected(requested):
    with pytest.raises(ForbiddenError):
        authorize_permissions(ceiling=[], instance=[], requested=requested)


def test_instance_configuration_does_not_accept_user_secret_references():
    from uuid import uuid4

    with pytest.raises(ValidationError):
        InstanceConfiguration(installation_id=uuid4(), secret_refs={"key": str(uuid4())})
