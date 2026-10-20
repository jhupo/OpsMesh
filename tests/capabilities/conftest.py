"""Capability tests use the same explicit static contracts as application bootstrap."""

import pytest


@pytest.fixture(autouse=True)
def capability_contracts():
    from opsmesh.bootstrap.capabilities import assemble_capability_validation

    assemble_capability_validation()
