"""Check authenticated identity ownership and immutability."""

from dataclasses import FrozenInstanceError

import pytest

from opsmesh_channels import UserContext


def test_caller_copies_identity_collections():
    groups = {"support"}
    roles = ["member"]
    caller = UserContext(id="api:alice", groups=groups, roles=roles)
    groups.clear()
    roles.append("admin")

    assert caller.groups == frozenset({"support"})
    assert caller.roles == frozenset({"member"})


def test_caller_identity_cannot_be_reassigned():
    caller = UserContext(id="api:alice")

    with pytest.raises(FrozenInstanceError):
        caller.id = "api:admin"


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"id": ""}, ValueError),
        ({"id": 42}, ValueError),
        ({"id": "alice", "name": None}, TypeError),
        ({"id": "alice", "groups": "admin"}, TypeError),
        ({"id": "alice", "roles": [""]}, ValueError),
        ({"id": "alice", "roles": [42]}, ValueError),
    ],
)
def test_invalid_identity_is_rejected(fields, error):
    with pytest.raises(error):
        UserContext(**fields)
