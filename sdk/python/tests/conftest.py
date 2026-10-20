"""Shared HTTP response fixtures for independent and combined SDK test runs."""

from typing import Any

import pytest


@pytest.fixture
def capability_version_payload() -> dict[str, Any]:
    return {
        "definition_id": "11111111-2222-3333-4444-555555555555",
        "version_id": "66666666-7777-8888-9999-aaaaaaaaaaaa",
        "version": 1,
        "status": "draft",
        "configuration": {"instructions": "Implement carefully."},
        "resource_manifest": [],
        "dependencies": [],
        "flow_templates": [],
        "digest": None,
        "package_digest": None,
        "package_size": None,
        "package_filename": None,
        "published_at": None,
        "published_by_user_id": None,
        "created_at": "2026-10-17T00:00:00Z",
    }

