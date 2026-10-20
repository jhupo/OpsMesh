"""Public version identifiers must be usable without querying the business database."""

from uuid import UUID

import pytest

from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account


@pytest.mark.parametrize(
    ("resource", "configuration"),
    [
        (
            "tools",
            {
                "execution_mode": "sdk_export",
                "entrypoint": "log_stats:exports",
                "operations": [{"name": "count_errors"}],
            },
        ),
        (
            "mcp",
            {
                "transport": "remote",
                "endpoint": "https://example.com/mcp",
                "tools": [{"name": "search"}],
            },
        ),
    ],
)
def test_published_version_response_can_create_binding(
    app_client, database_sessions, resource, configuration
):
    token = register_account(database_sessions, email=f"{resource}-versions@example.com")
    headers = {"X-Session-ID": str(token)}
    root = "/api/v1/capabilities"
    created = app_client.post(
        f"{root}/definitions",
        headers=headers,
        json={
            "key": "log-analysis",
            "name": "Log analysis",
            "declaration": {
                "type": "tool" if resource == "tools" else resource,
                "configuration": configuration,
            },
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    version_id = draft["version_id"]
    assert UUID(version_id) != UUID(draft["definition_id"])
    definition_root = f"{root}/definitions/{draft['definition_id']}/versions"

    updated = app_client.put(
        f"{root}/definitions/{draft['definition_id']}/draft",
        headers=headers,
        json={
            "declaration": {
                "type": "tool" if resource == "tools" else resource,
                "configuration": configuration,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version_id"] == version_id
    published = app_client.post(f"{definition_root}/1/publish", headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["version_id"] == version_id

    binding = app_client.post(
        "/api/v1/orchestration/capability-bindings",
        headers=headers,
        json={
            "name": "log-analysis",
            "version_id": published.json()["version_id"],
            "configuration": {},
        },
    )
    assert binding.status_code == 201, binding.text
    assert binding.json()["version_id"] == version_id
    assert binding.json()["version_digest"] == published.json()["digest"]

    next_draft = app_client.put(
        f"{root}/definitions/{draft['definition_id']}/draft",
        headers=headers,
        json={
            "declaration": {
                "type": "tool" if resource == "tools" else resource,
                "configuration": configuration,
            }
        },
    )
    assert next_draft.status_code == 200, next_draft.text
    assert next_draft.json()["version"] == 2
    assert UUID(next_draft.json()["version_id"]) not in {
        UUID(version_id),
        UUID(draft["definition_id"]),
    }
    read = app_client.get(f"{definition_root}/1", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["version_id"] == version_id
    listed = app_client.get(definition_root, headers=headers)
    assert listed.status_code == 200, listed.text
    assert {item["version"]: item["version_id"] for item in listed.json()} == {
        1: version_id,
        2: next_draft.json()["version_id"],
    }
