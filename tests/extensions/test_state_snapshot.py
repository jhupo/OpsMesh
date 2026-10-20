"""Malformed snapshots are rejected before any namespace cleanup can begin."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.extensions.schemas import StateSnapshot


@pytest.mark.parametrize(
    "entries",
    [
        [{}],
        [{"key": "x", "value": None}],
        [{"key": "x", "value": {"deleted": "false", "value": 1}}],
        [{"key": "x", "value": {"deleted": False}}],
        [
            {"key": "x", "value": {"deleted": False, "value": 1}},
            {"key": "x", "value": {"deleted": False, "value": 2}},
        ],
    ],
)
def test_malformed_entries_are_rejected_by_the_public_snapshot_contract(entries):
    with pytest.raises(ValidationError):
        StateSnapshot(installation_id=uuid4(), version_id=uuid4(), revision=1, entries=entries)
